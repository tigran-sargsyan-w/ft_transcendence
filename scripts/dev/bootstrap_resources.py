#!/usr/bin/env python3
"""Prepare Compose-compatible Docker networks/volumes for a fresh Ubuntu VM.

Never creates containers, starts services, or changes existing resources.
Does not initialize Vault or PostgreSQL. All modifications are explicit via apply.
"""

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP_PROJECT = "transcendence-app-dev"
VAULT_PROJECT = "transcendence-vault-dev"

# This manifest is intentionally strict. Changes to Compose topology must be
# reviewed here and in dev-preflight.py before they affect existing volumes.
NETWORKS = {
    "transcendence-vault-dev_vault_private": (VAULT_PROJECT, "vault_private", True),
    "transcendence-app-dev_default": (APP_PROJECT, "default", False),
    "transcendence-app-dev_collector_control": (APP_PROJECT, "collector_control", True),
}
VOLUMES = {
    "transcendence-vault-dev_vault_data": (VAULT_PROJECT, "vault_data"),
    "transcendence-vault-dev_backend_agent_auth": (VAULT_PROJECT, "backend_agent_auth"),
    "transcendence-vault-dev_backend_rendered": (VAULT_PROJECT, "backend_rendered"),
    "transcendence-app-dev_postgres_data": (APP_PROJECT, "postgres_data"),
    "transcendence-app-dev_migrator_vault_auth": (APP_PROJECT, "migrator_vault_auth"),
    "transcendence-app-dev_migrator_vault_rendered": (APP_PROJECT, "migrator_vault_rendered"),
    "transcendence-app-dev_postgres_provisioner_vault_auth": (
        APP_PROJECT, "postgres_provisioner_vault_auth"
    ),
    "transcendence-app-dev_postgres_provisioner_rendered": (
        APP_PROJECT, "postgres_provisioner_rendered"
    ),
}


class ResourceError(Exception):
    """A failure safe to report without exposing secret-bearing output."""


class ResourceInspectTimeout(ResourceError):
    """Only a read-only Docker inspection timeout is eligible for retry."""


# Strictly bounded retries for idempotent Docker metadata reads; default
# provisioning/Compose operations keep their original 40s timeout.
INSPECT_TIMEOUT = 5
INSPECT_ATTEMPTS = 3
INSPECT_RETRY_DELAY = 0.25


def command(args, *, env=None, timeout=40, allow_missing=False):
    try:
        result = subprocess.run(
            args, env=env, capture_output=True, timeout=timeout, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ResourceInspectTimeout(f"Command timed out: {args[0]}") from exc
    except OSError as exc:
        raise ResourceError(f"Command unavailable: {args[0]}") from exc
    if result.returncode:
        if allow_missing and result.returncode == 1:
            return None
        raise ResourceError(f"Docker command failed: {args[0]} (exit {result.returncode})")
    return result.stdout


def decode_json(blob):
    try:
        return json.loads(blob)
    except (UnicodeError, ValueError, TypeError) as exc:
        raise ResourceError("Invalid Docker JSON response") from exc


def compose_model(project, relative_path):
    tls = Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state"))) / "transcendence/dev/tls"
    supplied = os.environ.get("FT_DEV_TLS_DIR")
    if supplied and Path(supplied).resolve() != tls.resolve():
        raise ResourceError("Unexpected FT_DEV_TLS_DIR: use the standard development state path")
    # docker compose interpolates the user's .env by default; override it with
    # an empty env-file and a small known environment for model validation.
    safe_env = {
        key: value for key, value in os.environ.items()
        if key in ("PATH", "HOME", "USER", "DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CONFIG", "XDG_RUNTIME_DIR")
    }
    safe_env.update({
        "FT_DEV_TLS_DIR": str(tls),
        "POSTGRES_USER": "transcendence",
        "POSTGRES_DB": "transcendence",
    })
    output = command([
        "docker", "compose", "--env-file", "/dev/null", "-p", project,
        "-f", str(ROOT / relative_path), "--profile", "*",
        "config", "--format", "json",
    ], env=safe_env)
    model = decode_json(output)
    if model.get("name") != project:
        raise ResourceError(f"Unexpected Compose project: {project}")
    return model


def validate_compose_contract():
    app = compose_model(APP_PROJECT, "compose.yml")
    vault = compose_model(VAULT_PROJECT, "infra/vault/server/compose.yml")

    expected_vault_volumes = {
        name: internal for name, (project, internal) in VOLUMES.items()
        if project == VAULT_PROJECT
    }
    expected_app_volumes = {
        name: internal for name, (project, internal) in VOLUMES.items()
        if project == APP_PROJECT
    }
    app_shared_volumes = {
        "backend_vault_auth": "transcendence-vault-dev_backend_agent_auth",
        "backend_vault_rendered": "transcendence-vault-dev_backend_rendered",
    }
    app_volumes = app.get("volumes") or {}
    vault_volumes = vault.get("volumes") or {}

    if set(vault_volumes) != set(expected_vault_volumes.values()):
        raise ResourceError("Vault Compose volume inventory changed; review bootstrap manifest")
    if set(app_volumes) != set(expected_app_volumes.values()) | set(app_shared_volumes):
        raise ResourceError("Application Compose volume inventory changed; review bootstrap manifest")

    for name, internal in expected_vault_volumes.items():
        item = vault_volumes[internal]
        if item.get("name") != name or item.get("external") is True:
            raise ResourceError(f"Unexpected Vault Compose volume model: {internal}")
    for name, internal in expected_app_volumes.items():
        item = app_volumes[internal]
        if item.get("name") != name or item.get("external") is True:
            raise ResourceError(f"Unexpected application Compose volume model: {internal}")
    for internal, name in app_shared_volumes.items():
        item = app_volumes[internal]
        if item.get("name") != name or item.get("external") is not True:
            raise ResourceError(f"Unexpected external Compose volume model: {internal}")

    expected_app_networks = {"default", "collector_control", "vault_private"}
    expected_vault_networks = {"vault_private"}
    if (set(app.get("networks") or {}) != expected_app_networks or
            set(vault.get("networks") or {}) != expected_vault_networks):
        raise ResourceError("Compose network inventory changed; review bootstrap manifest")
    for project, model in ((APP_PROJECT, app), (VAULT_PROJECT, vault)):
        for internal, item in (model.get("networks") or {}).items():
            name = ("transcendence-vault-dev_vault_private" if internal == "vault_private"
                    else f"{APP_PROJECT}_{internal}")
            if item.get("name") != name or item.get("external") is not True:
                raise ResourceError(f"Unexpected external Compose network model: {project}/{internal}")

    print("PASS: Compose resource inventory and external network contract", flush=True)


def inspect(kind, name):
    # The only retried calls are read-only inspections of our exact manifest.
    if not (kind == "network" and name in NETWORKS or
            kind == "volume" and name in VOLUMES):
        raise ResourceError("Unexpected Docker resource inspection")
    raw = None
    for attempt in range(INSPECT_ATTEMPTS):
        try:
            raw = command(["docker", kind, "inspect", name],
                          allow_missing=True, timeout=INSPECT_TIMEOUT)
            break
        except ResourceInspectTimeout:
            if attempt + 1 == INSPECT_ATTEMPTS:
                raise ResourceError(f"Docker {kind} inspection repeatedly timed out") from None
            time.sleep(INSPECT_RETRY_DELAY)
    if raw is None:
        return None
    result = decode_json(raw)
    if not isinstance(result, list) or len(result) != 1:
        raise ResourceError(f"Ambiguous Docker {kind}: {name}")
    return result[0]


def validate_existing_volume(name, data):
    project, internal = VOLUMES[name]
    labels = data.get("Labels") or {}
    if (data.get("Name") != name or data.get("Driver") != "local" or
            labels.get("com.docker.compose.project") != project or
            labels.get("com.docker.compose.volume") != internal):
        raise ResourceError(f"Refusing incompatible existing volume: {name}")


def validate_existing_network(name, data):
    project, internal, private = NETWORKS[name]
    labels = data.get("Labels") or {}
    if (data.get("Name") != name or data.get("Driver") != "bridge" or
            data.get("Internal") is not private or
            labels.get("com.docker.compose.project") != project or
            labels.get("com.docker.compose.network") != internal):
        raise ResourceError(f"Refusing incompatible existing network: {name}")


def scan():
    existing = {}
    for kind, manifest in (("network", NETWORKS), ("volume", VOLUMES)):
        for name in manifest:
            data = inspect(kind, name)
            if data is not None:
                if kind == "network":
                    validate_existing_network(name, data)
                else:
                    validate_existing_volume(name, data)
                print(f"KEEP: {kind} {name}", flush=True)
            else:
                print(f"TODO: {kind} {name}", flush=True)
            existing[(kind, name)] = data is not None
    return existing


def create_missing(existing):
    # Existing resources have been inspected BEFORE any mutation. Never remove
    # or replace anything even if later steps fail; rerunning is safe.
    for name, (project, internal, private) in NETWORKS.items():
        if existing[("network", name)]:
            continue
        args = [
            "docker", "network", "create", "--driver", "bridge",
            "--label", f"com.docker.compose.project={project}",
            "--label", f"com.docker.compose.network={internal}",
        ]
        if private:
            args.append("--internal")
        args.append(name)
        command(args)
        created = inspect("network", name)
        if created is None:
            raise ResourceError(f"New network not found after creation: {name}")
        validate_existing_network(name, created)
        print(f"CREATED: network {name}", flush=True)

    for name, (project, internal) in VOLUMES.items():
        if existing[("volume", name)]:
            continue
        command([
            "docker", "volume", "create", "--driver", "local",
            "--label", f"com.docker.compose.project={project}",
            "--label", f"com.docker.compose.volume={internal}",
            name,
        ])
        created = inspect("volume", name)
        if created is None:
            raise ResourceError(f"New volume not found after creation: {name}")
        validate_existing_volume(name, created)
        print(f"CREATED: volume {name}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("plan", "check", "apply"))
    args = parser.parse_args()

    try:
        validate_compose_contract()
        print("=== Docker resource inventory ===", flush=True)
        existing = scan()
        if args.mode == "plan":
            print("RESULT: RESOURCE PLAN COMPLETE (read-only)")
            return 0
        if args.mode == "check":
            if not all(existing.values()):
                print("RESULT: DOCKER RESOURCES INCOMPLETE (read-only)")
                return 1
            print("RESULT: DOCKER RESOURCES READY (read-only)")
            return 0
        create_missing(existing)
        verified = scan()
        if not all(verified.values()):
            raise ResourceError("Missing required Docker resources after creation")
        print("RESULT: DOCKER RESOURCES READY")
        return 0
    except ResourceError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        print("RESULT: RESOURCE BOOTSTRAP REFUSED (no resources removed)", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
