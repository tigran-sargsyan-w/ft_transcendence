#!/usr/bin/env python3
"""Phase 5B: start only the existing Vault-backed NestJS backend.

Prerequisites: Phase 5A Agent (healthy), PostgreSQL and reviewed migrations.
The standard Compose service is created only when absent. Already-created
containers are identity-verified, retained and, if stopped, restarted without
recreation. No other application service is started; no credentials are read.
"""

import argparse
import fcntl
import subprocess
import sys
import time
from pathlib import Path

import bootstrap_backend_agent as agent
import bootstrap_postgres as pg

NAME = "transcendence-app-dev-backend-1"
SERVICE = "backend"
EXPECTED_IMAGE = "transcendence-app-dev-backend"
DATABASE_FILE = "/run/secrets/backend/database_url"
DEFAULT_NETWORK = "transcendence-app-dev_default"

# Runs INSIDE the already running backend container. It tests real Nest/Prisma
# health but does not print response data, environment values or credentials.
HTTP_HEALTH_PROBE = r"""
(async () => {
  const res = await fetch('http://127.0.0.1:3000/api/v1/health', {
    signal: AbortSignal.timeout(5000),
  });
  if (res.status !== 200) process.exitCode = 1;
  else {
    const body = await res.json();
    if (body?.data?.status !== 'ok' || body?.data?.database !== 'ok')
      process.exitCode = 1;
  }
})().catch(() => { process.exitCode = 1; });
"""


class BackendBootstrapError(Exception):
    """Public errors contain no raw output, DSNs or tokens."""


def run(argv, *, env=None, timeout=90):
    try:
        result = subprocess.run(
            argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=env, timeout=timeout, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise BackendBootstrapError(
            f"Backend command unavailable or timed out: {argv[0]}"
        ) from None
    if result.returncode:
        raise BackendBootstrapError(
            f"Backend command failed: {argv[0]} "
            f"(exit {result.returncode}; output withheld)"
        )
    return result


def container():
    return pg.exact_container(NAME)


def required_mount(item, destination, *, kind, source, readonly):
    matches = [m for m in item.get("Mounts") or []
               if m.get("Destination") == destination]
    if len(matches) != 1:
        return False
    m = matches[0]
    actual = m.get("Name") if kind == "volume" else m.get("Source")
    return (m.get("Type") == kind and actual == str(source)
            and m.get("RW") is (not readonly))


def verify_identity(item):
    if not isinstance(item, dict):
        raise BackendBootstrapError("Invalid backend container inspection")
    cfg = item.get("Config") or {}
    labels = cfg.get("Labels") or {}
    host = item.get("HostConfig") or {}
    expected = {
        "com.docker.compose.project": pg.PROJECT,
        "com.docker.compose.service": SERVICE,
        "com.docker.compose.project.config_files": str(pg.COMPOSE_FILE),
        "com.docker.compose.project.working_dir": str(pg.ROOT),
    }
    if (item.get("Name") != "/" + NAME
            or any(labels.get(k) != v for k, v in expected.items())
            or cfg.get("User") != "10001:20000"
            or cfg.get("Image") not in (EXPECTED_IMAGE, EXPECTED_IMAGE + ":latest")):
        raise BackendBootstrapError("Unexpected NestJS backend container identity")

    if (not required_mount(
                item, DATABASE_FILE.rsplit("/", 1)[0], kind="volume",
                source=agent.RENDERED_VOLUME, readonly=True)
            or not required_mount(
                item, "/app/prisma", kind="bind",
                source=pg.ROOT / "backend/prisma", readonly=True)):
        raise BackendBootstrapError("Unexpected backend mounts: secret or Prisma directory")

    mounts = item.get("Mounts") or []
    if len(mounts) != 2:
        raise BackendBootstrapError("Unexpected additional backend mounts")
    nets = (item.get("NetworkSettings") or {}).get("Networks") or {}
    opts = host.get("SecurityOpt") or []
    caps = host.get("CapDrop") or []
    if (set(nets) != {DEFAULT_NETWORK}
            or host.get("ReadonlyRootfs") is not True
            or host.get("Privileged") is not False
            or host.get("PortBindings")
            or not any(str(x).lower().startswith("no-new-privileges") for x in opts)
            or "all" not in [str(x).lower() for x in caps]):
        raise BackendBootstrapError("Unexpected backend networks or runtime privileges")

    entries = cfg.get("Env") or []
    if (f"DATABASE_URL_FILE={DATABASE_FILE}" not in entries
            or any(str(e).startswith((
                "DATABASE_URL=", "VAULT_TOKEN=", "POSTGRES_PASSWORD=", "PGPASSWORD=",
            )) for e in entries)):
        raise BackendBootstrapError("Unexpected database credential environment")

    command = cfg.get("Cmd") or []
    if (not isinstance(command, list)
            or "wait-for-database-secret.sh" not in " ".join(command)
            or "npm run start:prod" not in " ".join(command)):
        raise BackendBootstrapError("Unexpected NestJS startup command")
    return item


def healthy(item):
    state = item.get("State") or {}
    return (state.get("Running") is True
            and (state.get("Health") or {}).get("Status") == "healthy")


def verify_http_health():
    run(["docker", "exec", NAME, "node", "-e", HTTP_HEALTH_PROBE], timeout=20)
    print("PASS: NestJS /api/v1/health confirms PostgreSQL and migrations", flush=True)


def wait_backend(*, attempts=90, delay=2):
    for index in range(attempts):
        item = container()
        if item is None:
            raise BackendBootstrapError("Backend container disappeared")
        verify_identity(item)
        if healthy(item):
            verify_http_health()
            print("PASS: Vault-backed NestJS backend healthy", flush=True)
            return
        state = item.get("State") or {}
        if state.get("Status") in ("exited", "dead"):
            raise BackendBootstrapError("Backend process exited; inspect local Docker logs")
        if index + 1 < attempts:
            time.sleep(delay)
    raise BackendBootstrapError("NestJS backend did not become healthy (no cleanup performed)")


def require_prerequisites():
    # Phase 5A itself verifies Vault, secret delivery, database roles and
    # migration checksums. Backend can be started only after these succeed.
    if agent.main(["check"]) != 0:
        raise BackendBootstrapError("Backend Vault Agent or database prerequisites are incomplete")


def start_or_keep():
    existing = container()
    if existing is not None:
        verify_identity(existing)
        state = (existing.get("State") or {}).get("Status")
        if state == "running":
            print(f"KEEP: {NAME}", flush=True)
        elif state in ("created", "exited"):
            run(["docker", "start", NAME], timeout=90)
            print(f"START: {NAME}", flush=True)
        else:
            raise BackendBootstrapError("Unexpected existing backend container state")
        return

    # No Compose 'up' of other services. --no-recreate ensures that any
    # concurrent/unexpected pre-existing container cannot be recreated.
    run(agent.app_compose(
        "up", "-d", "--no-deps", "--no-recreate", "--build", SERVICE,
    ), env=agent.safe_env(), timeout=1200)
    existing = container()
    if existing is None:
        raise BackendBootstrapError("NestJS backend not created by Compose")
    verify_identity(existing)
    print(f"CREATED: {NAME}", flush=True)


def apply():
    with pg.LOCK.open("rb") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise BackendBootstrapError("Provisioning or migration workflow already running") from None
        # Revalidate before starting; fail closed on existing unknown identity.
        require_prerequisites()
        start_or_keep()
        wait_backend()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("plan", "check", "apply"))
    args = parser.parse_args(argv)
    try:
        if args.mode == "plan":
            item = container()
            if item is None:
                print("TODO: NestJS backend not created (read-only)")
            else:
                verify_identity(item)
                state = item.get("State") or {}
                health = (state.get("Health") or {}).get("Status", "none")
                print(f"EXISTS: NestJS backend ({state.get('Status')}, health={health})")
            print("RESULT: NESTJS BACKEND PLAN COMPLETE (read-only)")
            return 0
        require_prerequisites()
        if args.mode == "check":
            item = container()
            if item is None:
                raise BackendBootstrapError("NestJS backend missing")
            verify_identity(item)
            if not healthy(item):
                raise BackendBootstrapError("NestJS backend not healthy")
            verify_http_health()
            print("RESULT: NESTJS BACKEND READY (read-only)")
            return 0
        apply()
        print("RESULT: NESTJS BACKEND READY (apply)")
        return 0
    except (BackendBootstrapError, agent.BackendAgentError,
            pg.PostgresBootstrapError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        print("RESULT: NESTJS BACKEND STOPPED (no resources removed)", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
