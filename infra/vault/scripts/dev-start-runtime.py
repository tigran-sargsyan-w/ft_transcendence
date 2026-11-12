#!/usr/bin/env python3
"""Resume an already bootstrapped Vault-backed development stack (#115)."""

import argparse
import fcntl
import getpass
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
APP = "transcendence-app-dev"
VAULT = "transcendence-vault-dev"

ORDER = (
    (VAULT, "vault"),
    (APP, "postgres"),
    (APP, "docker-proxy"),
    (APP, "collector"),
    (APP, "graph-engine"),
    (APP, "vault-backend-agent"),
    (APP, "backend"),
    (APP, "frontend"),
    (APP, "nginx"),
)

HEALTH_REQUIRED = {
    "postgres",
    "graph-engine",
    "vault-backend-agent",
    "backend",
}


class StartupError(Exception):
    pass


def execute(args, *, data=None, timeout=35, ok=(0,)):
    try:
        result = subprocess.run(
            args,
            input=data,
            capture_output=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise StartupError(
            f"Command unavailable or timed out: {args[0]}"
        ) from exc

    if result.returncode not in ok:
        raise StartupError(
            f"Command failed: {args[0]} (exit {result.returncode})"
        )

    return result.stdout


def docker_json(*args):
    try:
        return json.loads(execute(["docker", *args]))
    except (ValueError, TypeError) as exc:
        raise StartupError("Invalid Docker JSON response") from exc


def name(project, service):
    return f"{project}-{service}-1"


def inspect(project, service):
    container = name(project, service)
    values = docker_json("inspect", container)

    if len(values) != 1:
        raise StartupError(
            f"Expected exactly one container: {container}"
        )

    value = values[0]
    labels = value.get("Config", {}).get("Labels") or {}

    expected = ROOT / (
        "infra/vault/server/compose.yml"
        if project == VAULT
        else "compose.yml"
    )

    if (
        labels.get("com.docker.compose.project") != project
        or labels.get("com.docker.compose.service") != service
        or labels.get("com.docker.compose.project.config_files")
        != str(expected)
    ):
        raise StartupError(
            f"Container not unified: {container}"
        )

    return value


def preflight():
    print("=== Read-only preflight ===", flush=True)

    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "infra/vault/scripts/dev-preflight.py"),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )

    if result.returncode:
        print(result.stdout, end="", flush=True)
        print(result.stderr, end="", file=sys.stderr, flush=True)
        raise StartupError(
            "Preflight failed; no containers started"
        )

    # Existing preflight also accepts legacy container origins.
    # inspect() deliberately rejects them for normal startup.
    print("PASS: Preflight passed", flush=True)


def wait_ready(project, service, seconds=120):
    for _ in range(seconds):
        state = inspect(project, service)["State"]
        health = (state.get("Health") or {}).get("Status")

        if state.get("Running") and health != "unhealthy":
            if (
                service not in HEALTH_REQUIRED
                or health == "healthy"
            ):
                return

        if state.get("Status") in ("dead", "exited"):
            raise StartupError(
                f"Container stopped unexpectedly: {service}"
            )

        time.sleep(1)

    raise StartupError(f"Health timeout: {service}")


def ensure_running(project, service):
    container = name(project, service)
    state = inspect(project, service)["State"]["Status"]

    if state == "running":
        print(f"KEEP: {container}", flush=True)

    elif state in ("exited", "created"):
        execute(
            ["docker", "start", container],
            timeout=60,
        )
        print(f"START: {container}", flush=True)

    else:
        raise StartupError(
            f"Unexpected container state ({state}): {container}"
        )

    wait_ready(project, service)


def vault_status(seconds=45):
    container = name(VAULT, "vault")

    for _ in range(seconds):
        try:
            raw = execute(
                [
                    "docker", "exec", container,
                    "vault", "status", "-format=json",
                ],
                ok=(0, 2),
                timeout=10,
            )

            state = json.loads(raw)

            if isinstance(state.get("sealed"), bool):
                return state

        except (StartupError, ValueError, TypeError):
            pass

        time.sleep(1)

    raise StartupError("Vault status not available")


def verify_vault():
    state = vault_status()

    if state.get("initialized") is not True:
        raise StartupError(
            "Vault not initialized; automatic init is forbidden"
        )

    if (state.get("n"), state.get("t")) != (3, 2):
        raise StartupError(
            "Unexpected Vault Shamir threshold"
        )

    if state.get("seal_type") not in (None, "shamir"):
        raise StartupError("Unexpected Vault seal type")

    if state["sealed"]:
        if state.get("progress", 0) != 0:
            raise StartupError(
                "Partial unseal exists; manual recovery required"
            )

        print(
            "Vault sealed: enter two ORIGINAL Shamir shares (hidden).",
            flush=True,
        )

        if not sys.stderr.isatty():
            raise StartupError(
                "Interactive terminal required for Vault unseal"
            )

        for index in (1, 2):
            share = getpass.getpass(
                f"Original unseal key {index}: "
            )

            if not share:
                raise StartupError("Empty unseal key")

            try:
                execute(
                    [
                        "docker", "exec", "-i",
                        name(VAULT, "vault"),
                        "vault", "write", "sys/unseal", "-",
                    ],
                    data=json.dumps({"key": share}).encode(),
                    timeout=15,
                )
            finally:
                del share

            state = vault_status()

            if index == 1 and (
                not state["sealed"]
                or state.get("progress") != 1
            ):
                raise StartupError(
                    "Unexpected first unseal result"
                )

            if index == 2 and state["sealed"]:
                raise StartupError(
                    "Vault remains sealed; stop and inspect manually"
                )

    state = vault_status()

    if (
        state.get("sealed") is not False
        or state.get("storage_type") != "raft"
    ):
        raise StartupError(
            "Vault not ready with Raft storage"
        )

    print(
        "PASS: Existing Vault initialized and unsealed",
        flush=True,
    )


def verify_app():
    execute([
        "docker", "exec",
        name(APP, "vault-backend-agent"),
        "sh", "-ec",
        "test -s /vault/runtime/agent/token && "
        "test -s /vault/rendered/database_url",
    ])

    execute([
        "docker", "exec",
        name(APP, "backend"),
        "sh", "-ec",
        "test -s /run/secrets/backend/database_url && "
        "test -r /run/secrets/backend/database_url",
    ])

    proxy_ping = r'''
const http = require("node:http");
const req = http.get(
  process.env.DOCKER_API_URL + "/_ping",
  { timeout: 5000 },
  res => {
    let data = "";
    res.on("data", chunk => data += chunk);
    res.on("end", () => {
      if (res.statusCode !== 200 || data.trim() !== "OK") {
        process.exitCode = 1;
      }
    });
  }
);
req.on("timeout", () => req.destroy());
req.on("error", () => process.exitCode = 1);
'''

    execute([
        "docker", "exec",
        name(APP, "collector"),
        "node", "-e", proxy_ping,
    ], timeout=15)

    for suffix in ("/", "/api/v1/health"):
        execute([
            "curl",
            "--silent",
            "--show-error",
            "--fail",
            "--insecure",
            "--noproxy", "*",
            "--connect-timeout", "5",
            "--max-time", "15",
            "--resolve",
            "transcendence.test:8443:127.0.0.1",
            "--output", "/dev/null",
            "https://transcendence.test:8443" + suffix,
        ], timeout=20)

    print(
        "PASS: Vault secret delivery, Docker Proxy and HTTPS API",
        flush=True,
    )


def run_start():
    # Reuse the existing migration/provisioning lock.
    lock = ROOT / "infra/vault/scripts/run-migrations.sh"

    with lock.open("rb") as fp:
        try:
            fcntl.flock(
                fp.fileno(),
                fcntl.LOCK_EX | fcntl.LOCK_NB,
            )
        except BlockingIOError as exc:
            raise StartupError(
                "Migration/provisioning workflow is already running"
            ) from exc

        preflight()

        for project, service in ORDER:
            ensure_running(project, service)

            if project == VAULT:
                verify_vault()

        verify_app()
        preflight()

    print(
        "RESULT: EXISTING STACK READY "
        "(no recreation, no migrations)",
        flush=True,
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "mode",
        nargs="?",
        choices=("start", "plan"),
        default="start",
    )

    args = parser.parse_args(argv)

    try:
        if args.mode == "plan":
            preflight()

            for project, service in ORDER:
                state = inspect(project, service)["State"]
                health = (
                    state.get("Health") or {}
                ).get("Status", "none")

                print(
                    f"{service}: {state['Status']}; health={health}"
                )

            print("RESULT: STARTUP PLAN VALID (read-only)")
        else:
            run_start()

        return 0

    except (
        StartupError,
        OSError,
        subprocess.SubprocessError,
    ) as exc:
        print("FAIL:", exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
