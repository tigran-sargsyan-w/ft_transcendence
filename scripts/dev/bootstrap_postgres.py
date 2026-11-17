#!/usr/bin/env python3
"""Phase 4C: first-time PostgreSQL 18 development cluster bootstrap.

An isolated, ephemeral PostgreSQL container initializes an EMPTY managed volume
using a one-time password file. The administrator's TCP password is removed
before that temporary container is stopped. The persistent, Vault-only Compose
service is then created without any bootstrap password in its configuration.

No PostgreSQL migrations, application roles, or Vault secrets are modified.
Existing clusters are never reinitialized, and ambiguous data is refused.
"""

import argparse
import fcntl
import json
import os
import secrets
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROJECT = "transcendence-app-dev"
NAME = PROJECT + "-postgres-1"
COMPOSE_FILE = ROOT / "compose.yml"
VOLUME = PROJECT + "_postgres_data"
NETWORK = PROJECT + "_default"
IMAGE = "postgres:18-bookworm"
BOOT_CONTAINER = "ft-transcendence-pg-bootstrap"
STATE = Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state"))) / "transcendence/dev"
LOCK = ROOT / "infra/vault/scripts/run-migrations.sh"

# Only non-secret SQL and image arguments are ever passed in argv.
INSPECT_SQL = """
SELECT CASE WHEN
  current_database() = 'transcendence'
  AND current_user = 'transcendence'
  AND current_setting('server_version_num')::int >= 180000
  AND current_setting('server_version_num')::int < 190000
  AND (SELECT rolsuper AND rolcanlogin AND rolpassword IS NULL
       FROM pg_authid WHERE rolname = 'transcendence') IS TRUE
  AND NOT EXISTS (
      SELECT 1 FROM pg_hba_file_rules
      WHERE type LIKE 'host%' AND auth_method = 'trust'
  )
THEN 'ok' ELSE 'invalid' END;
"""


class PostgresBootstrapError(Exception):
    """User-facing errors do not include subprocess output or credentials."""


def call(args, *, stdin=None, timeout=60, env=None, accepted=(0,)):
    try:
        result = subprocess.run(
            args, input=stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=timeout, check=False, env=env,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PostgresBootstrapError(
            f"PostgreSQL bootstrap command unavailable/timed out: {args[0]}"
        ) from exc
    if result.returncode not in accepted:
        raise PostgresBootstrapError(
            f"PostgreSQL bootstrap command failed: {args[0]} "
            f"(exit {result.returncode}; output withheld)"
        )
    return result.stdout


def docker_json(*args):
    try:
        return json.loads(call(["docker", *args]))
    except (ValueError, TypeError, UnicodeError) as exc:
        raise PostgresBootstrapError("Invalid Docker JSON") from exc


def exact_container(name):
    names = call([
        "docker", "container", "ls", "--all", "--filter", f"name=^/{name}$",
        "--format", "{{.Names}}",
    ], timeout=25).decode("utf-8").splitlines()
    if not names:
        return None
    if names != [name]:
        raise PostgresBootstrapError("Unexpected Docker container inventory")
    items = docker_json("container", "inspect", name)
    if not isinstance(items, list) or len(items) != 1:
        raise PostgresBootstrapError("Unexpected PostgreSQL inspect result")
    return items[0]


def verify_identity(item):
    labels = (item.get("Config") or {}).get("Labels") or {}
    if (
        item.get("Name") != "/" + NAME
        or labels.get("com.docker.compose.project") != PROJECT
        or labels.get("com.docker.compose.service") != "postgres"
        or labels.get("com.docker.compose.project.config_files") != str(COMPOSE_FILE)
        or labels.get("com.docker.compose.project.working_dir") != str(ROOT)
    ):
        raise PostgresBootstrapError("Unexpected PostgreSQL Compose container identity")
    mounts = [m for m in item.get("Mounts") or []
              if m.get("Destination") == "/var/lib/postgresql"]
    if (len(mounts) != 1 or mounts[0].get("Type") != "volume"
            or mounts[0].get("Name") != VOLUME):
        raise PostgresBootstrapError("Unexpected PostgreSQL data volume mount")
    networks = (item.get("NetworkSettings") or {}).get("Networks") or {}
    if set(networks) != {NETWORK}:
        raise PostgresBootstrapError("Unexpected PostgreSQL network attachment")
    if (item.get("HostConfig") or {}).get("PortBindings"):
        raise PostgresBootstrapError("PostgreSQL must not publish host ports")
    env = (item.get("Config") or {}).get("Env") or []
    forbidden = ("POSTGRES_PASSWORD=", "POSTGRES_PASSWORD_FILE=", "POSTGRES_HOST_AUTH_METHOD=trust")
    if any(entry.startswith(forbidden[:2]) or entry == forbidden[2] for entry in env):
        raise PostgresBootstrapError("Unexpected permanent PostgreSQL bootstrap credentials")
    return item


def db_query(container, sql, *, timeout=30):
    data = call([
        "docker", "exec", "-i", container,
        "psql", "-X", "-A", "-t", "-v", "ON_ERROR_STOP=1",
        "-U", "transcendence", "-d", "transcendence",
    ], stdin=sql.encode("utf-8"), timeout=timeout)
    return data.decode("utf-8").strip()


def verify_database(container):
    if db_query(container, INSPECT_SQL) != "ok":
        raise PostgresBootstrapError(
            "PostgreSQL identity/authentication check failed; manual review required"
        )
    print("PASS: PostgreSQL 18, database identity, SCRAM host auth and passwordless-disabled admin", flush=True)


def volume_state():
    # A read-only, networkless probe; does not run the DB server or reveal files.
    probe = r'''set -eu
root=/var/lib/postgresql
cluster="$root/18/docker"
if [ -f "$cluster/PG_VERSION" ]; then
  IFS= read -r version < "$cluster/PG_VERSION"
  if [ "$version" = 18 ]; then printf 'initialized\n'; else printf 'partial\n'; fi
elif [ -n "$(find "$root" -mindepth 1 \( -type f -o -type l \) -print -quit)" ]; then
  printf 'partial\n'
else
  printf 'empty\n'
fi'''
    result = call([
        "docker", "run", "--rm", "--network", "none", "--read-only",
        "--mount", f"type=volume,src={VOLUME},dst=/var/lib/postgresql,readonly",
        "--entrypoint", "/bin/sh", IMAGE, "-ec", probe,
    ], timeout=240).decode("ascii", "strict").strip()
    if result not in ("empty", "initialized", "partial"):
        raise PostgresBootstrapError("Unexpected PostgreSQL data-volume state")
    return result


def wait_for_bootstrap(attempts=75):
    for _ in range(attempts):
        item = exact_container(BOOT_CONTAINER)
        if item is None or not (item.get("State") or {}).get("Running"):
            raise PostgresBootstrapError("Temporary PostgreSQL initialization container exited early")
        try:
            # docker-entrypoint.sh briefly runs a temporary Postgres during
            # initdb, then stops it before exec'ing the final server.
            # Do not treat that temporary socket as completion.
            call([
                "docker", "exec", BOOT_CONTAINER, "sh", "-ec",
                'test "$(cat /proc/1/comm)" = postgres && '
                'exec pg_isready -U transcendence -d transcendence',
            ], timeout=8)
            return
        except PostgresBootstrapError:
            time.sleep(2)
    raise PostgresBootstrapError("PostgreSQL initial server readiness timeout")


def initialize_empty_cluster():
    if exact_container(BOOT_CONTAINER) is not None:
        raise PostgresBootstrapError("Previous temporary PostgreSQL container exists; manual review required")
    if volume_state() != "empty":
        raise PostgresBootstrapError("Refusing to initialize a nonempty PostgreSQL data volume")

    private = STATE / "private"
    if not private.is_dir() or private.is_symlink():
        raise PostgresBootstrapError("Trusted private development directory missing")
    info = private.stat()
    if info.st_uid != os.getuid() or (info.st_mode & 0o077):
        raise PostgresBootstrapError("Trusted private directory permissions unsafe")

    with tempfile.TemporaryDirectory(prefix="pg-init-", dir=private) as temp:
        directory = Path(temp)
        directory.chmod(0o700)
        pwd_path = directory / "bootstrap-password"
        # The bind-mounted file must be readable by PostgreSQL's non-root UID.
        # Host access is protected by the private parent directory (0700).
        with pwd_path.open("xb") as fh:
            fh.write((secrets.token_urlsafe(48) + "\n").encode("ascii"))
            fh.flush()
            os.fsync(fh.fileno())
        pwd_path.chmod(0o644)

        args = [
            "docker", "run", "--rm", "--detach", "--name", BOOT_CONTAINER,
            "--network", "none", "--log-driver", "none",
            "--mount", f"type=volume,src={VOLUME},dst=/var/lib/postgresql",
            "--mount", f"type=bind,src={pwd_path},dst=/run/secrets/pg-bootstrap,readonly",
            "--env", "POSTGRES_USER=transcendence",
            "--env", "POSTGRES_DB=transcendence",
            "--env", "POSTGRES_PASSWORD_FILE=/run/secrets/pg-bootstrap",
            "--env", "POSTGRES_HOST_AUTH_METHOD=scram-sha-256",
            "--env", "POSTGRES_INITDB_ARGS=--auth-host=scram-sha-256",
            IMAGE,
        ]
        started = False
        try:
            call(args, timeout=360)
            started = True
            wait_for_bootstrap()
            # Local Unix-socket-only SQL: revoke admin password BEFORE exposing
            # PostgreSQL on the Compose network; other roles come in Phase 4D.
            db_query(BOOT_CONTAINER, "ALTER ROLE transcendence PASSWORD NULL;")
            verify_database(BOOT_CONTAINER)
            print("PASS: Fresh PostgreSQL cluster initialized without persistent bootstrap password", flush=True)
        finally:
            if started:
                call(["docker", "stop", "--time", "30", BOOT_CONTAINER], timeout=55)
    if volume_state() != "initialized":
        raise PostgresBootstrapError("PostgreSQL data volume unexpected after initial setup")


def compose_env():
    safe = {key: val for key, val in os.environ.items() if key in (
        "PATH", "HOME", "USER", "DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CONFIG", "XDG_RUNTIME_DIR"
    )}
    safe.update({
        "FT_DEV_TLS_DIR": str(STATE / "tls"),
        "POSTGRES_USER": "transcendence",
        "POSTGRES_DB": "transcendence",
    })
    return safe


def compose_args(*extra):
    return [
        "docker", "compose", "--env-file", "/dev/null", "-p", PROJECT,
        "-f", str(COMPOSE_FILE), *extra,
    ]


def wait_for_runtime():
    for _ in range(75):
        item = exact_container(NAME)
        if item is None:
            raise PostgresBootstrapError("PostgreSQL Compose container disappeared")
        state = item.get("State") or {}
        if (state.get("Health") or {}).get("Status") == "healthy":
            return
        if state.get("Status") in ("dead", "exited"):
            raise PostgresBootstrapError("PostgreSQL Compose container stopped")
        time.sleep(2)
    raise PostgresBootstrapError("PostgreSQL Compose health timeout")


def start_or_keep():
    item = exact_container(NAME)
    if item is not None:
        verify_identity(item)
        state = item.get("State") or {}
        if state.get("Running"):
            print(f"KEEP: {NAME}", flush=True)
        elif state.get("Status") in ("created", "exited"):
            call(["docker", "start", NAME], timeout=90)
            print(f"START: {NAME}", flush=True)
        else:
            raise PostgresBootstrapError("Unexpected PostgreSQL container state")
    else:
        call(compose_args("up", "-d", "--no-deps", "--build", "--wait", "postgres"),
             env=compose_env(), timeout=480)
        item = exact_container(NAME)
        if item is None:
            raise PostgresBootstrapError("PostgreSQL Compose container was not created")
        verify_identity(item)
        print(f"CREATED: {NAME}", flush=True)
    wait_for_runtime()
    verify_identity(exact_container(NAME))
    verify_database(NAME)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("plan", "check", "apply"))
    args = parser.parse_args(argv)
    try:
        item = exact_container(NAME)
        if args.mode == "plan":
            if item is None:
                print("TODO: PostgreSQL Compose container not created (read-only)")
            else:
                verify_identity(item)
                print(f"EXISTS: PostgreSQL Compose container ({(item.get('State') or {}).get('Status')})")
            print("RESULT: POSTGRESQL PLAN COMPLETE (read-only)")
            return 0
        if args.mode == "check":
            if item is None or not (item.get("State") or {}).get("Running"):
                raise PostgresBootstrapError("PostgreSQL Compose container is not running")
            verify_identity(item)
            wait_for_runtime()
            verify_database(NAME)
            print("RESULT: POSTGRESQL READY (read-only)")
            return 0
        # Coordinate with the existing Vault/PostgreSQL provisioning and
        # Prisma migration workflow. Never overlap bootstrap and migrations.
        with LOCK.open("rb") as lock_file:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise PostgresBootstrapError(
                    "PostgreSQL provisioning/migration workflow already running"
                ) from exc
            if item is None:
                state = volume_state()
                if state == "partial":
                    raise PostgresBootstrapError("PostgreSQL data volume partially initialized; manual recovery required")
                if state == "empty":
                    initialize_empty_cluster()
                else:
                    print("KEEP: Existing PostgreSQL 18 cluster; no reinitialization", flush=True)
            start_or_keep()
        print("RESULT: POSTGRESQL READY (apply)")
        return 0
    except (PostgresBootstrapError, UnicodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        print("RESULT: POSTGRESQL BOOTSTRAP STOPPED (no volumes removed)", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
