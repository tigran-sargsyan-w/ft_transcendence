#!/usr/bin/env python3
"""Phase 4E: apply reviewed Prisma migrations, then reconcile PostgreSQL grants.

Only existing trusted Vault/PostgreSQL containers are used. The established
run-migrations.sh owns credential delivery, Prisma migration and grant setup.
No backend/frontend start, Docker Compose down, password rotation or secret
printing. The migration file checksums and PostgreSQL ACLs are inspected both
before and after the workflow; unknown migration history fails closed.
"""

import argparse
import fcntl
import hashlib
import os
import re
import sys
from pathlib import Path

import bootstrap_postgres as pg
import bootstrap_postgres_roles as roles
import bootstrap_vault_config as config

ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = ROOT / "backend/prisma/migrations"
MIGRATOR_VOLUME = "transcendence-app-dev_migrator_vault_auth"
WORKFLOW = ROOT / "infra/vault/scripts/run-migrations.sh"
VAULT_IMAGE = "hashicorp/vault:2.2.1"

# Never query application rows, Vault values or any PostgreSQL password hash.
# Use the database administrator via a local Unix socket to inspect only
# schema and effective privileges of the two fixed, approved application roles.
TABLE_STATE_SQL = """
SELECT CASE
 WHEN to_regclass('public._prisma_migrations') IS NULL THEN 'absent'
 WHEN to_regclass('public.users') IS NULL
   OR to_regclass('public.sessions') IS NULL THEN 'partial'
 ELSE 'present' END;
"""

GRANTS_SQL = """
SELECT CASE WHEN
  (SELECT count(*) FROM pg_class c JOIN pg_roles r ON r.oid = c.relowner
     WHERE c.oid IN (to_regclass('public.users'),
                      to_regclass('public.sessions'),
                      to_regclass('public._prisma_migrations'))
       AND r.rolname = 'transcendence_migrator') = 3
  AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'transcendence_app'
    AND has_schema_privilege(oid, 'public', 'USAGE')
    AND NOT has_schema_privilege(oid, 'public', 'CREATE')
    AND COALESCE(has_table_privilege(oid, to_regclass('public.users'), 'SELECT'), false)
    AND COALESCE(has_table_privilege(oid, to_regclass('public.users'), 'INSERT'), false)
    AND COALESCE(has_table_privilege(oid, to_regclass('public.users'), 'UPDATE'), false)
    AND COALESCE(has_table_privilege(oid, to_regclass('public.users'), 'DELETE'), false)
    AND COALESCE(has_table_privilege(oid, to_regclass('public.sessions'), 'SELECT'), false)
    AND COALESCE(has_table_privilege(oid, to_regclass('public.sessions'), 'INSERT'), false)
    AND COALESCE(has_table_privilege(oid, to_regclass('public.sessions'), 'UPDATE'), false)
    AND COALESCE(has_table_privilege(oid, to_regclass('public.sessions'), 'DELETE'), false)
    AND COALESCE(has_table_privilege(oid, to_regclass('public._prisma_migrations'), 'SELECT'), false)
    AND NOT COALESCE(has_table_privilege(oid, to_regclass('public._prisma_migrations'), 'INSERT'), false)
    AND NOT COALESCE(has_table_privilege(oid, to_regclass('public._prisma_migrations'), 'UPDATE'), false)
    AND NOT COALESCE(has_table_privilege(oid, to_regclass('public._prisma_migrations'), 'DELETE'), false)
    AND NOT COALESCE(has_table_privilege(oid, to_regclass('public._prisma_migrations'), 'TRUNCATE'), false)
    AND NOT COALESCE(has_table_privilege(oid, to_regclass('public._prisma_migrations'), 'REFERENCES'), false)
    AND NOT COALESCE(has_table_privilege(oid, to_regclass('public._prisma_migrations'), 'TRIGGER'), false)
    AND NOT COALESCE(has_table_privilege(oid, to_regclass('public.users'), 'TRUNCATE'), false)
    AND NOT COALESCE(has_table_privilege(oid, to_regclass('public.users'), 'REFERENCES'), false)
    AND NOT COALESCE(has_table_privilege(oid, to_regclass('public.users'), 'TRIGGER'), false)
    AND NOT COALESCE(has_table_privilege(oid, to_regclass('public.sessions'), 'TRUNCATE'), false)
    AND NOT COALESCE(has_table_privilege(oid, to_regclass('public.sessions'), 'REFERENCES'), false)
    AND NOT COALESCE(has_table_privilege(oid, to_regclass('public.sessions'), 'TRIGGER'), false))
  AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'transcendence_migrator'
    AND has_schema_privilege(oid, 'public', 'CREATE'))
THEN 'ready' ELSE 'pending' END;
"""

HISTORY_SQL = """
SELECT concat_ws(chr(9), migration_name, checksum,
   CASE WHEN rolled_back_at IS NOT NULL THEN 'rolled_back'
        WHEN finished_at IS NOT NULL THEN 'applied'
        ELSE 'failed' END)
FROM public._prisma_migrations
ORDER BY started_at, id;
"""


class MigrationBootstrapError(Exception):
    """Only safe, generic diagnostics. Never expose subprocess output."""


def expected_migrations():
    """Approve exactly the migration SQL files from the current Git checkout."""
    if not MIGRATIONS.is_dir() or MIGRATIONS.is_symlink():
        raise MigrationBootstrapError("Prisma migration directory unavailable/unsafe")
    expected = {}
    for path in sorted(MIGRATIONS.iterdir()):
        if path.name == "migration_lock.toml":
            continue
        if (not path.is_dir() or path.is_symlink() or
                not re.fullmatch(r"\d{14}_[a-zA-Z0-9_]+", path.name)):
            raise MigrationBootstrapError("Unexpected Prisma migration entry")
        sql = path / "migration.sql"
        if not sql.is_file() or sql.is_symlink():
            raise MigrationBootstrapError("Missing or unsafe Prisma migration.sql")
        expected[path.name] = hashlib.sha256(sql.read_bytes()).hexdigest()
    if not expected:
        raise MigrationBootstrapError("No Prisma migrations found; refusing to bootstrap")
    return expected


def sql_value(sql, accepted):
    value = pg.db_query(pg.NAME, sql).strip()
    if value not in accepted:
        raise MigrationBootstrapError("Unexpected PostgreSQL inspection response")
    return value


def migration_state():
    """Return ready/pending; refuse unknown, broken or altered migration history.

    This check intentionally does not run migrations or change grants.
    """
    expected = expected_migrations()
    state = sql_value(TABLE_STATE_SQL, ("absent", "partial", "present"))
    if state == "absent":
        # A database with pre-existing application tables but no migration
        # history is ambiguous, not an empty database to migrate into.
        existing = pg.db_query(pg.NAME, """
SELECT CASE WHEN to_regclass('public.users') IS NULL
  AND to_regclass('public.sessions') IS NULL THEN 'empty' ELSE 'unknown' END;
""").strip()
        if existing != "empty":
            raise MigrationBootstrapError("Unmanaged PostgreSQL tables without Prisma history")
        return "pending"
    if state == "partial":
        raise MigrationBootstrapError("Partial Prisma schema; manual review required")

    result = pg.db_query(pg.NAME, HISTORY_SQL)
    applied = {}
    if result:
        for line in result.splitlines():
            parts = line.split("\t")
            if len(parts) != 3:
                raise MigrationBootstrapError("Unexpected Prisma migration record")
            name, checksum, status = parts
            if (name not in expected or checksum != expected[name]
                    or status != "applied" or name in applied):
                raise MigrationBootstrapError("Prisma migration history drift; manual review required")
            applied[name] = checksum
    if set(applied) != set(expected):
        return "pending"
    grants = sql_value(GRANTS_SQL, ("ready", "pending"))
    return "ready" if grants == "ready" else "pending"


def approle_inventory():
    probe = (
        "if [ -s /vault/auth/role_id ] && [ -r /vault/auth/role_id ] && "
        "[ -s /vault/auth/secret_id ] && [ -r /vault/auth/secret_id ]; "
        "then exit 0; fi; "
        "if [ ! -e /vault/auth/role_id ] && [ ! -e /vault/auth/secret_id ]; "
        "then exit 2; fi; exit 3"
    )
    result = roles.sanitized_run([
        "docker", "run", "--rm", "--network", "none", "--read-only",
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
        "--user", "100:1000", "--mount",
        f"type=volume,source={MIGRATOR_VOLUME},target=/vault/auth,readonly",
        "--entrypoint", "/bin/sh", VAULT_IMAGE, "-ec", probe,
    ], accepted=(0, 2, 3), timeout=60)
    if result.returncode == 0:
        return "present"
    if result.returncode == 2:
        return "missing"
    raise MigrationBootstrapError("Migrator AppRole credentials partially initialized/unsafe")


def ensure_migrator_approle(env):
    roles.sanitized_run(roles.app_compose(
        "--profile", "vault-setup", "run", "--rm", "--no-deps", "-T",
        "vault-migrator-storage-init",
    ), env=env, timeout=90)

    if approle_inventory() == "present":
        print("KEEP: Prisma migrator AppRole credentials", flush=True)
        return

    token = config.recovery_token()
    roles.sanitized_run(roles.app_compose(
        "--profile", "vault-setup", "run", "--rm", "--no-deps", "-T",
        "vault-migrator-provision",
    ), env=env, stdin=(token + "\n").encode(), timeout=120)
    if approle_inventory() != "present":
        raise MigrationBootstrapError("Prisma migrator AppRole credential delivery incomplete")
    print("CREATED: Prisma migrator AppRole credentials", flush=True)


def run_existing_migrations(env):
    # Parent holds the exact file-lock open description on fd9. The established
    # run-migrations.sh and its provision-postgres.sh child reuse it, so no
    # race or self-deadlock is introduced by the fresh-machine wrapper.
    roles.sanitized_run([
        "bash", str(WORKFLOW),
    ], env=env, pass_fds=(9,), timeout=1800)
    print("PASS: Existing Prisma migration and grants workflow", flush=True)


def apply_migrations():
    env = roles.safe_env()
    with pg.LOCK.open("rb") as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise MigrationBootstrapError("Migration/provisioning workflow already running") from exc
        prior = None
        try:
            try:
                prior = os.dup(9)
            except OSError:
                pass
            os.dup2(lock_file.fileno(), 9, inheritable=True)
            # Re-check while holding the migration lock; another caller could
            # have applied migrations between the initial plan and this step.
            if migration_state() == "ready":
                print("KEEP: Prisma migrations and PostgreSQL grants", flush=True)
                return
            ensure_migrator_approle(env)
            print("RUN: Existing Prisma migration and grants workflow", flush=True)
            run_existing_migrations(env)
        finally:
            if prior is None:
                os.close(9)
            else:
                os.dup2(prior, 9)
                os.close(prior)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("plan", "check", "apply"))
    args = parser.parse_args(argv)
    try:
        if args.mode == "plan":
            if pg.main(["plan"]) != 0:
                raise MigrationBootstrapError("PostgreSQL inventory unavailable")
            item = pg.exact_container(pg.NAME)
            if item is None or not (item.get("State") or {}).get("Running"):
                print("TODO: Prisma migrations not inspectable (read-only)")
            else:
                pg.verify_identity(item)
                state = migration_state()
                print("EXISTS: Prisma migrations and grants (read-only)" if state == "ready"
                      else "TODO: Prisma migrations/grants need apply (read-only)")
            print("RESULT: PRISMA MIGRATIONS PLAN COMPLETE (read-only)")
            return 0

        if roles.main(["check"]) != 0:
            raise MigrationBootstrapError("PostgreSQL roles/Vault prerequisites incomplete")
        if args.mode == "check":
            if migration_state() != "ready":
                raise MigrationBootstrapError("Prisma migrations or table privileges incomplete")
            print("RESULT: PRISMA MIGRATIONS READY (read-only)")
            return 0

        apply_migrations()
        if migration_state() != "ready":
            raise MigrationBootstrapError("Prisma migration/grant verification incomplete")
        print("RESULT: PRISMA MIGRATIONS READY (apply)")
        return 0
    except (MigrationBootstrapError, roles.RolesBootstrapError,
            pg.PostgresBootstrapError, config.ConfigError,
            config.vault.VaultBootstrapError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        print("RESULT: PRISMA MIGRATIONS STOPPED (no volumes removed)", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
