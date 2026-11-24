#!/usr/bin/env python3
"""Phase 6D: non-destructive repeated-start and persistent-state acceptance.

This is an OPERATOR-INITIATED development VM test, not a daemon or CI
provisioning command. It requires an already healthy Vault-backed stack.
Never creates/stops/removes containers, runs Compose, migrates or reads Vault
credentials. Only the trusted --start may act, and it must be a no-op here.
Docker inspect responses are kept in memory and never printed or saved.
"""

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys

if __package__:
    from . import bootstrap_vault_ui as ui
else:
    import bootstrap_vault_ui as ui

ROOT = Path(__file__).resolve().parents[2]
BOOTSTRAP = ROOT / "scripts/bootstrap.sh"
APP = "transcendence-app-dev"
VAULT = "transcendence-vault-dev"
CONTAINERS = (
    f"{VAULT}-vault-1",
    f"{APP}-postgres-1",
    f"{APP}-vault-backend-agent-1",
    f"{APP}-backend-1",
    f"{APP}-docker-proxy-1",
    f"{APP}-collector-1",
    f"{APP}-prometheus-1",
    f"{APP}-graph-engine-1",
    f"{APP}-frontend-1",
    f"{APP}-nginx-1",
    f"{APP}-vault-grafana-agent-1",
    f"{APP}-grafana-1",
)
VOLUMES = (
    f"{VAULT}_vault_data",
    f"{VAULT}_backend_agent_auth",
    f"{VAULT}_backend_rendered",
    f"{APP}_postgres_data",
    f"{APP}_prometheus_data",
    f"{APP}_grafana_data",
    f"{APP}_grafana_vault_auth",
    f"{APP}_grafana_vault_rendered",
    f"{APP}_migrator_vault_auth",
    f"{APP}_migrator_vault_rendered",
    f"{APP}_postgres_provisioner_vault_auth",
    f"{APP}_postgres_provisioner_rendered",
)
NETWORKS = (
    f"{VAULT}_vault_private",
    f"{APP}_default",
    f"{APP}_collector_control",
)

# This metadata-only query doesn't read application rows or database secrets.
# Hashing server-side avoids storing or printing migration names/checksums.
MIGRATION_FINGERPRINT = """
SELECT md5(COALESCE(string_agg(
  migration_name || ':' || checksum || ':' ||
  CASE WHEN rolled_back_at IS NOT NULL THEN 'rolled_back'
       WHEN finished_at IS NOT NULL THEN 'applied' ELSE 'failed' END,
  '|' ORDER BY started_at, id), ''))
FROM public._prisma_migrations;
"""


class AcceptanceError(Exception):
    """Error messages contain no output from docker, psql or Vault."""


def call(args, *, data=None, timeout=60):
    try:
        result = subprocess.run(
            args, input=data, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            cwd=ROOT, check=False, timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise AcceptanceError(f"Command unavailable or timed out: {args[0]}") from None
    if result.returncode:
        raise AcceptanceError(f"Command failed: {args[0]} (output withheld)")
    return result.stdout


def docker_json(*args):
    try:
        value = json.loads(call(["docker", *args]))
    except (ValueError, UnicodeError):
        raise AcceptanceError("Unexpected Docker inspection data") from None
    if not isinstance(value, list):
        raise AcceptanceError("Unexpected Docker inspection format")
    return value


def names_for(kind):
    output = call(["docker", kind, "ls", "-a", "--format", "{{.Names}}"] if kind == "container"
                  else ["docker", kind, "ls", "--format", "{{.Name}}"])
    try:
        names = output.decode("utf-8").splitlines()
    except UnicodeError:
        raise AcceptanceError(f"Invalid Docker {kind} inventory") from None
    prefix = (
        (f"{APP}-", f"{VAULT}-", f"{ui.PROJECT}-")
        if kind == "container"
        else (f"{APP}_", f"{VAULT}_", f"{ui.PROJECT}_")
    )
    return tuple(sorted(n for n in names if n.startswith(prefix)))


def inspected(kind, expected):
    # Never show full Docker inspect JSON: it can include container environment.
    found = docker_json(kind, "inspect", *expected) if kind != "container" else docker_json("container", "inspect", *expected)
    if len(found) != len(expected):
        raise AcceptanceError(f"Missing {kind} in Docker inventory")
    by_name = {}
    for entry in found:
        name = entry.get("Name")
        if kind == "container" and isinstance(name, str):
            name = name.removeprefix("/")
        if name not in expected or name in by_name:
            raise AcceptanceError(f"Unexpected or duplicate {kind} inspection")
        by_name[name] = entry
    if set(by_name) != set(expected):
        raise AcceptanceError(f"Incomplete {kind} inspection")
    return by_name


def migration_fingerprint():
    output = call([
        "docker", "exec", "-i", CONTAINERS[1], "psql", "-X", "-A", "-t",
        "-v", "ON_ERROR_STOP=1", "-U", "transcendence", "-d", "transcendence",
    ], data=MIGRATION_FINGERPRINT.encode("utf-8"), timeout=30)
    try:
        value = output.decode("ascii").strip()
    except UnicodeError:
        raise AcceptanceError("Invalid migration metadata inspection") from None
    if not re.fullmatch(r"[0-9a-f]{32}", value):
        raise AcceptanceError("Unexpected migration metadata fingerprint")
    return value



def vault_ui_inventory():
    """Verify optional gateway intent, identity and edge network."""
    try:
        enabled = ui.desired_enabled()
        gateway = ui.inspect()

        if gateway is None and enabled:
            raise AcceptanceError("Enabled Vault UI gateway missing")

        if gateway is not None:
            ui.verify_identity(gateway)

        edge_present = ui.EDGE in names_for("network")

        if gateway is not None and not edge_present:
            raise AcceptanceError("Vault UI edge network missing")

        if edge_present:
            ui.verify_network(ui.EDGE, ui.PROJECT, False)

        if gateway is not None:
            state = gateway.get("State") or {}
            running = (
                state.get("Running") is True
                and state.get("Status") == "running"
            )
            stopped = (
                state.get("Running") is False
                and state.get("Status") in ("created", "exited")
            )

            if enabled and not running:
                raise AcceptanceError("Enabled Vault UI not running")
            if not enabled and not stopped:
                raise AcceptanceError("Disabled Vault UI not stopped")
            if not isinstance(gateway.get("Id"), str) or not gateway["Id"]:
                raise AcceptanceError("Missing Vault UI Docker identity")

        return enabled, gateway, edge_present

    except (ui.GatewayError, OSError):
        raise AcceptanceError(
            "Vault UI gateway inventory not trusted"
        ) from None


def snapshot():
    """Select only non-secret immutable identities and lifecycle metadata."""
    containers = inspected("container", CONTAINERS)
    volumes = inspected("volume", VOLUMES)

    ui_enabled, gateway, edge_present = vault_ui_inventory()

    expected_networks = NETWORKS + (
        (ui.EDGE,) if edge_present else ()
    )
    networks = inspected("network", expected_networks)

    result = {
        "vault_ui_enabled": ui_enabled,
        "container_inventory": names_for("container"),
        "volume_inventory": names_for("volume"),
        "network_inventory": names_for("network"),
        "migration_history": migration_fingerprint(),
        "containers": {}, "volumes": {}, "networks": {},
    }
    for name, item in containers.items():
        state = item.get("State") or {}
        if state.get("Running") is not True or state.get("Status") != "running":
            raise AcceptanceError(f"Container not running: {name}")
        if not isinstance(item.get("Id"), str) or not item["Id"]:
            raise AcceptanceError(f"Missing Docker identity: {name}")
        result["containers"][name] = (
            item["Id"], item.get("Created"), item.get("RestartCount"),
            state.get("StartedAt"),
        )
    if gateway is not None:
        if ui.NAME not in result["container_inventory"]:
            raise AcceptanceError("Vault UI inventory mismatch")

        state = gateway.get("State") or {}
        result["containers"][ui.NAME] = (
            gateway["Id"],
            gateway.get("Created"),
            gateway.get("RestartCount"),
            state.get("StartedAt"),
            state.get("Status"),
            state.get("Running"),
        )

    if edge_present and ui.EDGE not in result["network_inventory"]:
        raise AcceptanceError("Vault UI network inventory mismatch")

    for name, item in volumes.items():
        result["volumes"][name] = (item.get("CreatedAt"), item.get("Driver"), item.get("Mountpoint"))
    for name, item in networks.items():
        result["networks"][name] = (item.get("Id"), item.get("Created"))
    return result


def compare(expected, current):
    if current["vault_ui_enabled"] != expected["vault_ui_enabled"]:
        raise AcceptanceError("Vault UI enabled state changed")

    # Prevent silent creation of additional project resources; no raw IDs printed.
    for group in ("container_inventory", "volume_inventory", "network_inventory", "migration_history"):
        if current[group] != expected[group]:
            raise AcceptanceError(f"Unexpected change: {group}")
    for group in ("containers", "volumes", "networks"):
        for name, value in expected[group].items():
            if current[group].get(name) != value:
                raise AcceptanceError(f"Unexpected {group} lifecycle change: {name}")


def trusted(flag):
    if flag not in ("--health-report", "--start"):
        raise AcceptanceError("Disallowed acceptance command")
    try:
        completed = subprocess.run(
            [str(BOOTSTRAP), flag], cwd=ROOT, check=False,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=480,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise AcceptanceError(f"Trusted {flag} unavailable or timed out") from None
    if completed.returncode:
        raise AcceptanceError(f"Trusted {flag} failed; inspect it manually")


def verify(*, runs=3, read_only=False):
    """Reject an unhealthy baseline; compare state after each safe no-op start."""
    if not 1 <= runs <= 5:
        raise AcceptanceError("Runs must be between 1 and 5")
    print("=== Development startup idempotency acceptance ===", flush=True)
    trusted("--health-report")
    baseline = snapshot()
    print("PASS: healthy baseline, container/volume/network/migration inventory recorded in memory", flush=True)
    if read_only:
        print("RESULT: IDEMPOTENCY BASELINE READY (read-only)", flush=True)
        return
    for index in range(1, runs + 1):
        print(f"RUN: guarded --start ({index}/{runs})", flush=True)
        trusted("--start")
        # Inspect immediately, before any health request; catch hidden restarts.
        compare(baseline, snapshot())
        trusted("--health-report")
        compare(baseline, snapshot())
        print(f"PASS: attempt {index}/{runs}; no recreation, restart or migration history drift", flush=True)
    print("RESULT: STARTUP IDEMPOTENCY VERIFIED", flush=True)


def trusted_initialize():
    """Explicit operator opt-in only, after a healthy baseline.

    Do not impose a parent-only timeout on the mutating orchestrator: its
    cancellation boundary must be allowed to finish the current critical write.
    """
    try:
        completed = subprocess.run(
            [str(BOOTSTRAP), "--initialize"], cwd=ROOT, check=False,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        raise AcceptanceError("Trusted initialize unavailable") from None
    if completed.returncode:
        raise AcceptanceError("Trusted initialize failed; inspect it manually")


def verify_initialize(*, runs=3):
    """Compare identities around the actual initialize workflow on a healthy VM."""
    if not 1 <= runs <= 5:
        raise AcceptanceError("Runs must be between 1 and 5")
    print("=== Development initialize idempotency acceptance ===", flush=True)
    trusted("--health-report")
    baseline = snapshot()
    for index in range(1, runs + 1):
        print(f"RUN: guarded --initialize ({index}/{runs})", flush=True)
        trusted_initialize()
        compare(baseline, snapshot())
        trusted("--health-report")
        compare(baseline, snapshot())
        print(f"PASS: initialize {index}/{runs}; no recreation, restart or migration history drift", flush=True)
    print("RESULT: INITIALIZE IDEMPOTENCY VERIFIED", flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=3)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check-only", action="store_true",
                      help="read-only baseline; do not invoke --start")
    mode.add_argument("--initialize", action="store_true",
                      help="opt-in repeated initialize on an already healthy development VM")
    args = parser.parse_args(argv)
    try:
        if os.geteuid() == 0:
            raise AcceptanceError("Run as ordinary developer, never sudo")
        if args.initialize:
            verify_initialize(runs=args.runs)
        else:
            verify(runs=args.runs, read_only=args.check_only)
        return 0
    except AcceptanceError as exc:
        print(f"ERROR: {exc}", file=sys.stderr, flush=True)
        print("RESULT: STARTUP ACCEPTANCE STOPPED (no cleanup attempted)", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
