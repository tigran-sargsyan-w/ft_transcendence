#!/usr/bin/env python3
"""Resume an already bootstrapped Vault-backed development stack (#115)."""

import argparse
import fcntl
import getpass
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from dev_migration_audit import audit_migrations, MigrationAuditError
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



def wait_frontend_ready(attempts=30, delay=2):
    """Wait until the existing Vite process actually accepts HTTP."""
    probe = r"""
const http = require('node:http');
const req = http.get(
  'http://127.0.0.1:5173/',
  { timeout: 3000 },
  res => {
    const ok = res.statusCode === 200;
    res.resume();
    res.on('end', () => {
      process.exitCode = ok ? 0 : 1;
    });
  }
);
req.on('timeout', () => req.destroy());
req.on('error', () => {
  process.exitCode = 1;
});
"""

    for attempt in range(attempts):
        try:
            execute(
                [
                    "docker", "exec",
                    name(APP, "frontend"),
                    "node", "-e", probe,
                ],
                timeout=8,
            )
            print("PASS: Frontend Vite HTTP ready", flush=True)
            return

        except StartupError:
            if attempt + 1 < attempts:
                time.sleep(delay)

    raise StartupError(
        "Frontend HTTP readiness timeout (port 5173)"
    )


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

    if project == APP and service == "frontend":
        wait_frontend_ready()


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



def wait_https_route(suffix, attempts=20, delay=2):
    """Wait for reverse-proxy HTTP readiness without response bodies."""
    last_status = "unavailable"

    for attempt in range(attempts):
        try:
            output = execute(
                [
                    "curl",
                    "--silent",
                    "--insecure",
                    "--noproxy", "*",
                    "--connect-timeout", "3",
                    "--max-time", "8",
                    "--resolve",
                    "transcendence.test:8443:127.0.0.1",
                    "--output", "/dev/null",
                    "--write-out", "%{http_code}",
                    "https://transcendence.test:8443" + suffix,
                ],
                timeout=12,
            )

            status = output.decode("ascii", errors="replace").strip()
            last_status = status if status.isdigit() else "invalid"

            if status == "200":
                print(
                    f"PASS: HTTPS route ready: {suffix}",
                    flush=True,
                )
                return

        except StartupError:
            last_status = "unavailable"

        if attempt + 1 < attempts:
            time.sleep(delay)

    raise StartupError(
        f"HTTPS readiness timeout: {suffix} "
        f"(last HTTP status: {last_status})"
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
        wait_https_route(suffix)

    print(
        "PASS: Vault secret delivery, Docker Proxy and HTTPS API",
        flush=True,
    )


def check_migrations_or_fail():
    pending = audit_migrations()

    if pending:
        for migration in pending:
            print(f"PENDING: {migration}", flush=True)

        raise StartupError(
            "Pending Prisma migrations; startup refused. "
            "Run the controlled migration workflow first."
        )

    print("PASS: Prisma migration history current", flush=True)


def run_migrations_with_lock(lock_fd):
    """Run controlled migrations using the already-held startup lock."""
    if os.environ.get("FT_APP_COMPOSE_OVERRIDE"):
        raise StartupError(
            "Compose overrides are not supported for routine migration"
        )

    env = os.environ.copy()
    env["FT_STARTUP_LOCK_FD"] = str(lock_fd)
    env["FT_EXISTING_RUNTIME_ONLY"] = "1"
    env["FT_APP_COMPOSE_PROJECT"] = APP
    env["POSTGRES_USER"] = "transcendence"
    env["POSTGRES_DB"] = "transcendence"
    env["FT_DEV_TLS_DIR"] = str(
        Path(env.get(
            "XDG_STATE_HOME",
            str(Path.home() / ".local/state"),
        )) / "transcendence/dev/tls"
    )

    print("=== Controlled migration workflow ===", flush=True)

    try:
        result = subprocess.run(
            [
                str(
                    ROOT / "infra/vault/scripts/run-migrations.sh"
                ),
            ],
            env=env,
            pass_fds=(lock_fd,),
            check=False,
        )
    except OSError as exc:
        raise StartupError(
            "Migration workflow could not be started"
        ) from exc

    if result.returncode:
        raise StartupError(
            "Migration workflow failed; Backend remains stopped"
        )

    print("PASS: Controlled migrations completed", flush=True)


def run_start(allow_migrations=False):
    # Reuse the existing migration/provisioning lock.
    did_migrate = False
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
            if project == APP and service == "backend":
                if allow_migrations:
                    pending = audit_migrations()

                    if pending:
                        for migration in pending:
                            print(f"PENDING: {migration}", flush=True)

                        backend_state = inspect(
                            APP, "backend"
                        )["State"]["Status"]

                        if backend_state == "running":
                            print(
                                "STOP: Backend before migrations",
                                flush=True,
                            )
                            execute(
                                [
                                    "docker", "stop", "--time", "20",
                                    name(APP, "backend"),
                                ],
                                timeout=45,
                            )
                        elif backend_state not in ("exited", "created"):
                            raise StartupError(
                                "Backend is in unexpected state"
                            )

                        run_migrations_with_lock(fp.fileno())
                        did_migrate = True

                        # Revalidate every local migration and checksum.
                        check_migrations_or_fail()
                    else:
                        print(
                            "PASS: No migrations required",
                            flush=True,
                        )
                else:
                    check_migrations_or_fail()

            ensure_running(project, service)

            if project == VAULT:
                verify_vault()

        verify_app()
        preflight()

    if did_migrate:
        print(
            "RESULT: EXISTING STACK READY "
            "(controlled migrations applied)",
            flush=True,
        )
    else:
        print(
            "RESULT: EXISTING STACK READY "
            "(no recreation, no migrations)",
            flush=True,
        )


def plan_runtime():
    """Read-only readiness plan; never start containers."""
    preflight()

    problems = []
    statuses = {}

    for project, service in ORDER:
        state = inspect(project, service)["State"]
        status = state.get("Status", "unknown")
        health = (state.get("Health") or {}).get(
            "Status", "none"
        )

        statuses[service] = (status, health)

        print(
            f"{service}: {status}; health={health}",
            flush=True,
        )

        if status != "running":
            problems.append(
                f"{service}: requires startup ({status})"
            )
        elif service in HEALTH_REQUIRED and health != "healthy":
            problems.append(
                f"{service}: health={health}"
            )
        elif health == "unhealthy":
            problems.append(
                f"{service}: unhealthy"
            )

    if statuses["vault"][0] == "running":
        try:
            vault = vault_status(seconds=5)
        except StartupError:
            problems.append("Vault status unavailable")
        else:
            if vault.get("initialized") is not True:
                problems.append("Vault not initialized")
            if vault.get("sealed") is not False:
                problems.append("Vault sealed")
            if (vault.get("n"), vault.get("t")) != (3, 2):
                problems.append("Unexpected Vault Shamir threshold")
            if vault.get("seal_type") not in (None, "shamir"):
                problems.append("Unexpected Vault seal type")
            if vault.get("storage_type") != "raft":
                problems.append("Vault storage is not Raft")

    if statuses["postgres"] == ("running", "healthy"):
        pending = audit_migrations()

        if pending:
            for migration in pending:
                print(f"PENDING: {migration}", flush=True)

            problems.append(
                "Prisma migrations required"
            )
        else:
            print(
                "PASS: Prisma migrations current",
                flush=True,
            )
    else:
        problems.append(
            "PostgreSQL must be healthy before migration audit"
        )

    # Read-only, one-shot application probes.
    # Normal startup retains its longer bounded retries.
    if statuses["frontend"][0] == "running":
        try:
            wait_frontend_ready(attempts=1, delay=0)
        except StartupError:
            problems.append("frontend: Vite HTTP not ready")

    if statuses["nginx"][0] == "running":
        for suffix in ("/", "/api/v1/health"):
            try:
                wait_https_route(suffix, attempts=1, delay=0)
            except StartupError:
                problems.append(
                    f"nginx: HTTPS route not ready: {suffix}"
                )

    if problems:
        for problem in problems:
            print(f"NOT READY: {problem}", flush=True)

        print(
            "RESULT: STARTUP PLAN NOT READY (read-only)",
            flush=True,
        )
        return 1

    print(
        "RESULT: STARTUP PLAN VALID (read-only)",
        flush=True,
    )
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "mode",
        nargs="?",
        choices=("start", "plan", "migrate"),
        default="start",
    )

    args = parser.parse_args(argv)

    try:
        if args.mode == "plan":
            return plan_runtime()
        else:
            run_start(
                allow_migrations=(args.mode == "migrate")
            )

        return 0

    except (
        StartupError,
        MigrationAuditError,
        OSError,
        subprocess.SubprocessError,
    ) as exc:
        print("FAIL:", exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
