#!/usr/bin/env python3
"""Phase 5A: provision the initial backend AppRole and start only Vault Agent.

No backend/frontend/nginx, PostgreSQL SQL changes, or credential rotation.
Existing AppRole identities and agent containers are always preserved. A
healthy agent must render the backend DSN for the unprivileged backend group.
"""

import argparse
import fcntl
import os
import subprocess
import sys
import time

import bootstrap_migrations as migrations
import bootstrap_postgres as pg
import bootstrap_postgres_roles as roles
import bootstrap_vault_config as config

NAME = "transcendence-app-dev-vault-backend-agent-1"
AUTH_VOLUME = "transcendence-vault-dev_backend_agent_auth"
RENDERED_VOLUME = "transcendence-vault-dev_backend_rendered"
PRIVATE_NETWORK = "transcendence-vault-dev_vault_private"
VAULT_IMAGE = "hashicorp/vault:2.2.1"
VAULT_COMPOSE = pg.ROOT / "infra/vault/server/compose.yml"


class BackendAgentError(Exception):
    """Public diagnostics are always independent of secret values."""


def run(args, *, stdin=None, env=None, timeout=90, accepted=(0,)):
    try:
        result = subprocess.run(
            args, input=stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=timeout, env=env, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise BackendAgentError(f"Command unavailable or timed out: {args[0]}") from None
    if result.returncode not in accepted:
        raise BackendAgentError(
            f"Command failed: {args[0]} (exit {result.returncode}; output withheld)"
        )
    return result


def safe_env():
    # Never inherit operator tokens, database URLs or other Docker Compose vars.
    env = roles.safe_env()
    env.pop("FT_EXISTING_RUNTIME_ONLY", None)
    return env


def vault_compose(*args):
    return [
        "docker", "compose", "--env-file", "/dev/null",
        "-p", "transcendence-vault-dev", "-f", str(VAULT_COMPOSE),
        *args,
    ]


def app_compose(*args):
    return roles.app_compose(*args)


def credential_inventory():
    """Read only metadata, not contents of backend role_id/secret_id."""
    # Docker run --mount would create a missing volume. Never allow an
    # ostensibly read-only check to create persistent Docker resources.
    run(["docker", "volume", "inspect", AUTH_VOLUME], timeout=25)
    probe = r'''set -eu
for f in role_id secret_id; do
  p="/vault/auth/$f"
  if [ -L "$p" ]; then exit 3; fi
done
if [ ! -e /vault/auth/role_id ] && [ ! -e /vault/auth/secret_id ]; then
  exit 2
fi
for f in role_id secret_id; do
  p="/vault/auth/$f"
  [ -f "$p" ] && [ -s "$p" ] && [ -r "$p" ] || exit 3
  [ "$(stat -c %a "$p")" = 400 ] || exit 3
  [ "$(stat -c %u "$p")" = 100 ] || exit 3
done
'''
    result = run([
        "docker", "run", "--rm", "--network", "none", "--read-only",
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
        "--user", "100:1000", "--mount",
        f"type=volume,source={AUTH_VOLUME},target=/vault/auth,readonly",
        "--entrypoint", "/bin/sh", VAULT_IMAGE, "-ec", probe,
    ], timeout=90, accepted=(0, 2, 3))
    if result.returncode == 0:
        return "present"
    if result.returncode == 2:
        return "missing"
    raise BackendAgentError("Backend AppRole volume contains partial or unsafe credentials")


def ensure_credentials(env):
    state = credential_inventory()
    if state == "present":
        print("KEEP: Backend AppRole credentials", flush=True)
        return
    # Existing Vault Compose helper creates a SecretID and places credentials
    # in the dedicated 0400 auth volume. Root token is sent only via stdin.
    token = config.recovery_token()
    run(vault_compose(
        "--profile", "setup", "run", "--rm", "--no-deps", "-T",
        "vault-backend-provision",
    ), stdin=(token + "\n").encode(), env=env, timeout=150)
    if credential_inventory() != "present":
        raise BackendAgentError("Backend AppRole provisioning did not produce safe credentials")
    print("CREATED: Backend AppRole credentials", flush=True)


def inspect_agent():
    return pg.exact_container(NAME)


def mount_ok(item, destination, volume, readonly):
    mounts = [m for m in item.get("Mounts", [])
              if m.get("Destination") == destination]
    return (len(mounts) == 1 and mounts[0].get("Type") == "volume"
            and mounts[0].get("Name") == volume
            and mounts[0].get("RW") is (not readonly))


def verify_identity(item):
    cfg = item.get("Config") or {}
    labels = cfg.get("Labels") or {}
    if (item.get("Name") != "/" + NAME
            or labels.get("com.docker.compose.project") != pg.PROJECT
            or labels.get("com.docker.compose.service") != "vault-backend-agent"
            or labels.get("com.docker.compose.project.config_files") != str(pg.COMPOSE_FILE)
            or labels.get("com.docker.compose.project.working_dir") != str(pg.ROOT)
            or cfg.get("User") != "100:20000"
            or cfg.get("Image") != VAULT_IMAGE):
        raise BackendAgentError("Unexpected backend Vault Agent Compose identity")
    if (not mount_ok(item, "/vault/auth", AUTH_VOLUME, readonly=True)
            or not mount_ok(item, "/vault/rendered", RENDERED_VOLUME, readonly=False)):
        raise BackendAgentError("Unexpected backend Vault Agent credential mounts")
    tls_mounts = [m for m in item.get("Mounts", [])
                  if m.get("Destination") == "/vault/tls/ca.crt"]
    if (len(tls_mounts) != 1 or tls_mounts[0].get("Type") != "bind"
            or tls_mounts[0].get("RW") is not False
            or tls_mounts[0].get("Source") != str(config.vault.STATE / "tls/ca.crt")):
        raise BackendAgentError("Unexpected backend Vault Agent trusted CA mount")
    networks = (item.get("NetworkSettings") or {}).get("Networks") or {}
    host = item.get("HostConfig") or {}
    if (set(networks) != {PRIVATE_NETWORK} or host.get("Privileged") is True
            or host.get("PortBindings") or
            any(x.startswith(("VAULT_TOKEN=", "DATABASE_URL="))
                for x in cfg.get("Env") or [])):
        raise BackendAgentError("Unexpected backend Vault Agent network or runtime privileges")
    return item


def verify_rendered_access():
    run(["docker", "volume", "inspect", RENDERED_VOLUME], timeout=25)
    # Backend user (10001:20000) can access the secret, with NO network and NO
    # file contents sent to stdout. Also rejects group-world-readable secrets.
    probe = r'''set -eu
p=/vault/rendered/database_url
[ ! -L "$p" ] && [ -f "$p" ] && [ -s "$p" ] && [ -r "$p" ] || exit 1
[ "$(stat -c %a "$p")" = 440 ] || exit 1
[ "$(stat -c %g "$p")" = 20000 ] || exit 1
'''
    run([
        "docker", "run", "--rm", "--network", "none", "--read-only",
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
        "--user", "10001:20000", "--mount",
        f"type=volume,source={RENDERED_VOLUME},target=/vault/rendered,readonly",
        "--entrypoint", "/bin/sh", VAULT_IMAGE, "-ec", probe,
    ], timeout=90)
    print("PASS: Backend UID/GID can read Vault-rendered DSN (content not displayed)", flush=True)


def ready(item):
    state = item.get("State") or {}
    health = (state.get("Health") or {}).get("Status")
    return state.get("Running") is True and health == "healthy"


def wait_agent(attempts=60, delay=2):
    for index in range(attempts):
        item = inspect_agent()
        if item is None:
            raise BackendAgentError("Backend Vault Agent container disappeared")
        verify_identity(item)
        if ready(item):
            verify_rendered_access()
            print("PASS: Backend Vault Agent healthy", flush=True)
            return
        state = item.get("State") or {}
        if state.get("Status") in ("dead", "exited") or (
                state.get("Health") or {}).get("Status") == "unhealthy":
            raise BackendAgentError("Backend Vault Agent stopped or unhealthy; inspect locally")
        if index + 1 < attempts:
            time.sleep(delay)
    raise BackendAgentError("Backend Vault Agent health timeout (no cleanup attempted)")


def require_prerequisites():
    if migrations.main(["check"]) != 0:
        raise BackendAgentError("Prisma migrations/PostgreSQL/Vault prerequisites incomplete")


def start_or_keep(env):
    item = inspect_agent()
    if item is not None:
        verify_identity(item)
        status = (item.get("State") or {}).get("Status")
        if status == "running":
            print(f"KEEP: {NAME}", flush=True)
        elif status in ("exited", "created"):
            run(["docker", "start", NAME], timeout=60)
            print(f"START: {NAME}", flush=True)
        else:
            raise BackendAgentError("Unexpected backend Vault Agent container state")
        return
    run(app_compose("up", "-d", "--no-deps", "--no-recreate", "vault-backend-agent"),
        env=env, timeout=180)
    item = inspect_agent()
    if item is None:
        raise BackendAgentError("Backend Vault Agent was not created")
    verify_identity(item)
    print(f"CREATED: {NAME}", flush=True)


def apply():
    env = safe_env()
    with pg.LOCK.open("rb") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise BackendAgentError("Provisioning or migration workflow already running") from None
        # Fail closed on unexpected existing containers before touching auth.
        existing = inspect_agent()
        if existing is not None:
            verify_identity(existing)
        ensure_credentials(env)
        start_or_keep(env)
        wait_agent()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("plan", "check", "apply"))
    args = parser.parse_args(argv)
    try:
        if args.mode == "plan":
            item = inspect_agent()
            if item is None:
                print("TODO: Backend Vault Agent not created (read-only)")
            else:
                verify_identity(item)
                state = item.get("State") or {}
                health = (state.get("Health") or {}).get("Status", "none")
                print(f"EXISTS: Backend Vault Agent ({state.get('Status')}, health={health})")
            print("RESULT: BACKEND VAULT AGENT PLAN COMPLETE (read-only)")
            return 0
        require_prerequisites()
        if args.mode == "check":
            if credential_inventory() != "present":
                raise BackendAgentError("Backend AppRole credentials not initialized")
            item = inspect_agent()
            if item is None:
                raise BackendAgentError("Backend Vault Agent container missing")
            verify_identity(item)
            if not ready(item):
                raise BackendAgentError("Backend Vault Agent is not healthy")
            verify_rendered_access()
            print("RESULT: BACKEND VAULT AGENT READY (read-only)")
            return 0
        apply()
        print("RESULT: BACKEND VAULT AGENT READY (apply)")
        return 0
    except (BackendAgentError, pg.PostgresBootstrapError, config.ConfigError,
            config.vault.VaultBootstrapError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        print("RESULT: BACKEND VAULT AGENT STOPPED (no volumes removed)", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
