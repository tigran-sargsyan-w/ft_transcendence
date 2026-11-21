#!/usr/bin/env python3
"""Prepare and verify only the dedicated Grafana Vault Agent."""

import argparse
import fcntl
import sys
import time

import bootstrap_backend_agent as agent
import bootstrap_grafana_secrets as secrets
import bootstrap_postgres as pg
import bootstrap_resources as resources
import bootstrap_vault_config as config

SERVICE = "vault-grafana-agent"
NAME = f"{pg.PROJECT}-{SERVICE}-1"

IMAGE = "hashicorp/vault:2.2.1"
NETWORK = "transcendence-vault-dev_vault_private"

AUTH_VOLUME = f"{pg.PROJECT}_grafana_vault_auth"
RENDERED_VOLUME = f"{pg.PROJECT}_grafana_vault_rendered"

SCRIPT = pg.ROOT / "infra/vault/scripts/grafana-agent.sh"
AUTH_SCRIPT = pg.ROOT / "infra/vault/scripts/grafana-auth.sh"
AGENT_CONFIG = pg.ROOT / "infra/vault/agent/grafana.hcl"
CA_FILE = config.vault.STATE / "tls/ca.crt"

ENTRYPOINT = ["/bin/sh", "/vault/scripts/grafana-agent.sh"]
HEALTHCHECK = ["CMD", *ENTRYPOINT, "check"]


class GrafanaAgentError(Exception):
    """Credential-free diagnostics only."""


def container():
    return pg.exact_container(NAME)


def verify_identity(item):
    if not isinstance(item, dict):
        raise GrafanaAgentError("Grafana Vault Agent container missing")

    cfg = item.get("Config") or {}
    labels = cfg.get("Labels") or {}
    host = item.get("HostConfig") or {}
    networks = (item.get("NetworkSettings") or {}).get("Networks") or {}

    expected_labels = {
        "com.docker.compose.project": pg.PROJECT,
        "com.docker.compose.service": SERVICE,
        "com.docker.compose.project.config_files": str(pg.COMPOSE_FILE),
        "com.docker.compose.project.working_dir": str(pg.ROOT),
    }

    if (
        item.get("Name") != "/" + NAME
        or any(labels.get(k) != v for k, v in expected_labels.items())
        or cfg.get("Image") != IMAGE
        or cfg.get("User") != "100:20030"
        or cfg.get("Entrypoint") != ENTRYPOINT
        or cfg.get("Cmd") != ["start"]
        or (cfg.get("Healthcheck") or {}).get("Test") != HEALTHCHECK
    ):
        raise GrafanaAgentError("Unexpected Grafana Agent identity")

    if (
        set(networks) != {NETWORK}
        or host.get("Privileged") is not False
        or host.get("PortBindings")
        or host.get("ReadonlyRootfs") is not True
        or set(host.get("CapDrop") or []) != {"ALL"}
        or host.get("CapAdd")
        or "no-new-privileges:true" not in (host.get("SecurityOpt") or [])
        or set(host.get("Tmpfs") or {}) != {"/tmp", "/vault/runtime"}
    ):
        raise GrafanaAgentError("Unexpected Grafana Agent runtime privileges")

    expected = {
        "/vault/config/grafana.hcl": (
            "bind", str(AGENT_CONFIG), False,
        ),
        "/vault/scripts/grafana-agent.sh": (
            "bind", str(SCRIPT), False,
        ),
        "/vault/tls/ca.crt": (
            "bind", str(CA_FILE), False,
        ),
        "/vault/auth": (
            "volume", AUTH_VOLUME, False,
        ),
        "/vault/rendered": (
            "volume", RENDERED_VOLUME, True,
        ),
    }

    mounts = [
        m for m in item.get("Mounts", [])
        if m.get("Type") != "tmpfs"
    ]

    if len(mounts) != len(expected):
        raise GrafanaAgentError("Unexpected Grafana Agent mount count")

    actual = {}

    for mount in mounts:
        kind = mount.get("Type")
        source = mount.get("Name") if kind == "volume" else mount.get("Source")

        actual[mount.get("Destination")] = (
            kind, source, mount.get("RW"),
        )

    if actual != expected:
        raise GrafanaAgentError("Unexpected Grafana Agent mounts")

    environment = cfg.get("Env") or []

    if (
        "VAULT_ADDR=https://vault:8200" not in environment
        or "VAULT_CACERT=/vault/tls/ca.crt" not in environment
        or any(value.startswith((
            "VAULT_TOKEN=",
            "DATABASE_URL=",
            "GF_SECURITY_ADMIN_PASSWORD=",
            "GF_SECURITY_SECRET_KEY=",
        )) for value in environment)
    ):
        raise GrafanaAgentError("Unexpected Grafana Agent environment")

    return item


def require_prerequisites():
    resources.validate_compose_contract()

    if not all(resources.scan(batch=True).values()):
        raise GrafanaAgentError(
            "Required Docker resources missing; prepare resources explicitly"
        )

    if secrets.main(["check"]) != 0:
        raise GrafanaAgentError("Grafana Vault secrets are not ready")


def credential_inventory():
    # Never allow a read-only probe to create a named volume.
    agent.run(
        ["docker", "volume", "inspect", AUTH_VOLUME],
        timeout=25,
    )

    result = agent.run([
        "docker", "run", "--rm",
        "--network", "none",
        "--read-only",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges:true",
        "--user", "100:1000",
        "--mount",
        f"type=volume,source={AUTH_VOLUME},target=/vault/auth,readonly",
        "--mount",
        f"type=bind,src={AUTH_SCRIPT},dst=/vault/scripts/grafana-auth.sh,readonly",
        "--entrypoint", "/bin/sh",
        IMAGE, "/vault/scripts/grafana-auth.sh", "check",
    ], timeout=60, accepted=(0, 2))

    return "present" if result.returncode == 0 else "missing"


def prepare_storage(env):
    agent.run(agent.app_compose(
        "--profile", "vault-setup",
        "run", "--rm", "--no-deps", "-T",
        "vault-grafana-storage-init",
    ), env=env, timeout=90)

    print("PASS: Grafana credential storage permissions", flush=True)


def ensure_credentials(env):
    state = credential_inventory()

    if state == "present":
        print("KEEP: Grafana AppRole credentials", flush=True)
        return

    if container() is not None:
        raise GrafanaAgentError(
            "Existing Agent has missing credentials; operator review required"
        )

    token = config.recovery_token()

    agent.run(agent.app_compose(
        "--profile", "vault-setup",
        "run", "--rm", "--no-deps", "-T",
        "vault-grafana-provision",
        "apply",
    ), stdin=(token + "\n").encode(), env=env, timeout=150)

    if credential_inventory() != "present":
        raise GrafanaAgentError("Grafana AppRole provisioning not verified")

    print("CREATED: Grafana AppRole credentials", flush=True)


def ready(item):
    state = item.get("State") or {}

    return (
        state.get("Running") is True
        and (state.get("Health") or {}).get("Status") == "healthy"
    )


def verify_rendered_access():
    agent.run(
        ["docker", "volume", "inspect", RENDERED_VOLUME],
        timeout=25,
    )

    # Verify that the real Grafana UID/GID can read both files.
    # No network, AppRole credentials or secret output.
    probe = r"""
set -eu
for field in admin_password secret_key; do
    p="/vault/rendered/$field"
    [ ! -L "$p" ] && [ -f "$p" ] && [ -s "$p" ] && [ -r "$p" ]
    [ "$(stat -c '%u:%g:%a' "$p")" = 100:20030:440 ]
    [ "$(wc -c < "$p")" -eq 64 ]
    grep -Eq '^[0-9a-f]{64}$' "$p"
done
"""

    agent.run([
        "docker", "run", "--rm",
        "--network", "none",
        "--read-only",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges:true",
        "--user", "472:20030",
        "--mount",
        f"type=volume,source={RENDERED_VOLUME},target=/vault/rendered,readonly",
        "--entrypoint", "/bin/sh",
        IMAGE, "-ec", probe,
    ], timeout=60)

    print("PASS: Grafana UID/GID can read rendered secrets", flush=True)


def verify_delivery():
    agent.run(
        ["docker", "exec", NAME, *ENTRYPOINT, "check"],
        timeout=25,
    )

    verify_rendered_access()

    print("PASS: Actual Grafana Vault secret delivery", flush=True)


def start_or_keep(env):
    item = container()

    if item is not None:
        verify_identity(item)

        status = (item.get("State") or {}).get("Status")

        if status == "running":
            print(f"KEEP: {NAME}", flush=True)
            return

        if status not in ("created", "exited"):
            raise GrafanaAgentError("Unexpected Grafana Agent state")

        agent.run(["docker", "start", NAME], timeout=90)
        print(f"START: {NAME}", flush=True)
        return

    agent.run(agent.app_compose(
        "--profile", "monitoring",
        "up", "-d", "--no-deps", "--no-recreate",
        SERVICE,
    ), env=env, timeout=180)

    verify_identity(container())

    print(f"CREATED: {NAME}", flush=True)


def wait_agent(attempts=60, delay=2):
    for attempt in range(attempts):
        item = verify_identity(container())

        if ready(item):
            verify_delivery()
            return

        status = (item.get("State") or {}).get("Status")

        if status in ("dead", "exited"):
            raise GrafanaAgentError("Grafana Vault Agent stopped unexpectedly")

        if attempt + 1 < attempts:
            time.sleep(delay)

    raise GrafanaAgentError("Grafana Vault Agent readiness timeout")


def check():
    require_prerequisites()

    if credential_inventory() != "present":
        raise GrafanaAgentError("Grafana AppRole credentials missing")

    item = verify_identity(container())

    if not ready(item):
        raise GrafanaAgentError("Grafana Vault Agent not healthy")

    verify_delivery()


def apply():
    env = agent.safe_env()

    with pg.LOCK.open("rb") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise GrafanaAgentError(
                "Another provisioning workflow is running"
            ) from None

        require_prerequisites()

        existing = container()

        if existing is not None:
            verify_identity(existing)

        prepare_storage(env)
        ensure_credentials(env)
        start_or_keep(env)
        wait_agent()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("plan", "check", "apply"))
    args = parser.parse_args(argv)

    try:
        if args.mode == "plan":
            item = container()

            if item is None:
                print("TODO: Grafana Vault Agent not created")
            else:
                verify_identity(item)
                print("EXISTS: Trusted Grafana Vault Agent")

            return 0

        if args.mode == "check":
            check()
        else:
            apply()

        print(
            f"RESULT: GRAFANA VAULT AGENT READY ({args.mode})",
            flush=True,
        )

        return 0

    except (
        GrafanaAgentError,
        agent.BackendAgentError,
        resources.ResourceError,
        pg.PostgresBootstrapError,
        config.ConfigError,
        config.vault.VaultBootstrapError,
        OSError,
    ) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        print(
            "RESULT: GRAFANA VAULT AGENT STOPPED",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
