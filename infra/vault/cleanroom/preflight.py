#!/usr/bin/env python3
"""Fail-closed isolation checks for clean-room Docker Compose projects."""

import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]

APP_OVERRIDE = ROOT / "infra/vault/cleanroom/app.override.yml"
VAULT_OVERRIDE = ROOT / "infra/vault/cleanroom/vault.override.yml"

PRODUCTION_TLS = (ROOT / "infra/vault/server/tls").resolve()


def require_env(name):
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Required variable missing: {name}")
    return value


def compose_config(command):
    result = subprocess.run(
        command + ["config", "--format", "json"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise RuntimeError("Docker Compose configuration failed")
    return json.loads(result.stdout)


def check(condition, message):
    if not condition:
        raise RuntimeError(message)


def main():
    app_project = require_env("FT_APP_COMPOSE_PROJECT")
    vault_project = require_env("FT_CR_VAULT_PROJECT")
    tls_input = Path(require_env("FT_CR_TLS_DIR"))
    test_password = require_env("FT_CR_PG_PASSWORD")
    override = Path(require_env("FT_APP_COMPOSE_OVERRIDE")).resolve()

    check(
        app_project.startswith("ft-cr-")
        and vault_project.startswith("ft-cr-")
        and app_project != vault_project,
        "Unsafe clean-room project names",
    )

    check(
        override == APP_OVERRIDE.resolve(),
        "Unexpected application Compose override",
    )

    check(
        tls_input.is_absolute()
        and tls_input.is_dir()
        and not tls_input.is_symlink(),
        "Clean-room TLS directory must be a real absolute directory",
    )

    tls_dir = tls_input.resolve()

    check(
        tls_dir != PRODUCTION_TLS
        and PRODUCTION_TLS not in tls_dir.parents
        and tls_dir not in PRODUCTION_TLS.parents,
        "Clean-room TLS directory overlaps production TLS",
    )

    vault = compose_config([
        "docker", "compose",
        "-p", vault_project,
        "-f", str(ROOT / "infra/vault/server/compose.yml"),
        "-f", str(VAULT_OVERRIDE),
        "--profile", "setup",
        "--profile", "maintenance",
    ])

    app = compose_config([
        "docker", "compose",
        "-p", app_project,
        "-f", str(ROOT / "compose.yml"),
        "-f", str(ROOT / "compose.vault.yml"),
        "-f", str(APP_OVERRIDE),
        "--profile", "vault-setup",
        "--profile", "vault-maintenance",
    ])

    check(vault["name"] == vault_project, "Vault project mismatch")
    check(app["name"] == app_project, "Application project mismatch")
    print("PASS: Independent Compose project identities")

    network_name = f"{vault_project}_vault_private"

    check(
        vault["networks"]["vault_private"]["name"] == network_name
        and vault["networks"]["vault_private"]["internal"] is True
        and not vault["networks"]["vault_private"].get("external", False),
        "Vault server network is not isolated",
    )

    check(
        app["networks"]["vault_private"]["name"] == network_name
        and app["networks"]["vault_private"].get("external") is True,
        "Application does not reference the test Vault network",
    )

    for name, network in app["networks"].items():
        if name != "vault_private":
            check(
                not network.get("external", False)
                and network["name"].startswith(app_project + "_"),
                f"Unexpected application network: {name}",
            )

    print("PASS: Dedicated internal Vault network")

    for name, volume in vault["volumes"].items():
        check(
            not volume.get("external", False)
            and volume["name"].startswith(vault_project + "_"),
            f"Unsafe Vault volume: {name}",
        )

    for name, volume in app["volumes"].items():
        if name in ("backend_vault_auth", "backend_vault_rendered"):
            suffix = (
                "backend_agent_auth"
                if name == "backend_vault_auth"
                else "backend_rendered"
            )
            check(
                volume.get("external") is True
                and volume["name"] == f"{vault_project}_{suffix}",
                f"Unsafe shared volume: {name}",
            )
        else:
            check(
                not volume.get("external", False)
                and volume["name"].startswith(app_project + "_"),
                f"Unsafe application volume: {name}",
            )

    print("PASS: All Docker volumes isolated")

    expected_tls_mounts = {
        "vault": ("/vault/tls", tls_dir, True),
        "vault-storage-permissions": (
            "/vault/tls", tls_dir, False
        ),
        "vault-backend-provision": (
            "/vault/tls/ca.crt", tls_dir / "ca.crt", True
        ),
        "vault-bootstrap": (
            "/vault/tls/ca.crt", tls_dir / "ca.crt", True
        ),
        "vault-tls-files": (
            "/vault/tls", tls_dir, False
        ),
    }

    for name, (target, source, readonly) in expected_tls_mounts.items():
        check(
            name in vault["services"],
            f"Vault service missing from preflight: {name}",
        )

        mounts = [
            m for m in vault["services"][name].get("volumes", [])
            if m["target"] == target
        ]

        check(
            len(mounts) == 1
            and mounts[0]["type"] == "bind"
            and Path(mounts[0]["source"]).resolve() == source
            and mounts[0].get("read_only", False) == readonly,
            f"Unsafe TLS mount in Vault service: {name}",
        )

    ca_mounts = 0

    for service in app["services"].values():
        for mount in service.get("volumes", []):
            if mount["target"] == "/vault/tls/ca.crt":
                ca_mounts += 1
                check(
                    mount["type"] == "bind"
                    and mount.get("read_only") is True
                    and Path(mount["source"]).resolve()
                        == tls_dir / "ca.crt",
                    "Unsafe application Vault CA mount",
                )

    check(ca_mounts >= 4, "Expected Vault CA mounts are missing")
    print("PASS: TLS files isolated from production")

    for project in (vault, app):
        for name, service in project["services"].items():
            check(
                not service.get("container_name"),
                f"Fixed container name: {name}",
            )
            check(
                not service.get("ports"),
                f"Published port: {name}",
            )

            for mount in service.get("volumes", []):
                source = mount.get("source", "")
                check(
                    source != "/var/run/docker.sock",
                    f"Docker socket exposed: {name}",
                )

                if mount.get("type") == "bind":
                    resolved = Path(source).resolve()
                    check(
                        resolved != PRODUCTION_TLS
                        and PRODUCTION_TLS not in resolved.parents,
                        f"Production TLS bind mount: {name}",
                    )

    for name in (
        "collector",
        "docker-proxy",
        "frontend",
        "graph-engine",
        "nginx",
    ):
        check(
            name not in app["services"],
            f"Nonessential service is active: {name}",
        )

    check(
        app["services"]["postgres"]["environment"]["POSTGRES_PASSWORD"]
        == test_password,
        "Test PostgreSQL password was not selected",
    )

    print("PASS: No published ports or Docker socket")
    print("PASS: Nonessential services excluded")
    print("PASS: Separate PostgreSQL password configured")
    print("PASS: Clean-room isolation preflight completed")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, KeyError, ValueError) as exc:
        raise SystemExit(f"FAIL: {exc}") from None
