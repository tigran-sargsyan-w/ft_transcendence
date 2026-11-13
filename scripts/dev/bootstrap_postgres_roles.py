#!/usr/bin/env python3
"""Phase 4D: provision two PostgreSQL roles using existing Vault tooling.

Initial provisioning only: never overwrite an active PostgreSQL password or
an existing AppRole credential. Uses the existing validated provisioning
workflow and its migration lock. Prisma migrations are a separate phase.
"""

import argparse
import fcntl
import os
import subprocess
import sys
from pathlib import Path

import bootstrap_postgres as pg
import bootstrap_vault_config as config
import bootstrap_vault_secrets as secret_bootstrap

ROOT = Path(__file__).resolve().parents[2]
ROLE_VOLUME = "transcendence-app-dev_postgres_provisioner_vault_auth"
VAULT_IMAGE = "hashicorp/vault:2.2.1"

# Only boolean role metadata and grants. Never read pg_authid.rolpassword into
# application memory: SQL returns only the string 'ready' or 'pending'.
ROLE_STATUS_SQL = r"""
SELECT CASE WHEN
    (SELECT count(*) FROM pg_authid
     WHERE rolname IN ('transcendence_app', 'transcendence_migrator')
       AND rolcanlogin
       AND rolpassword LIKE 'SCRAM-SHA-256$%'
       AND NOT (rolsuper OR rolcreatedb OR rolcreaterole OR
                rolreplication OR rolbypassrls)) = 2
    AND EXISTS (
        SELECT 1 FROM pg_roles WHERE rolname = 'transcendence_app'
          AND has_schema_privilege(oid, 'public', 'USAGE')
          AND NOT has_schema_privilege(oid, 'public', 'CREATE')
    )
    AND EXISTS (
        SELECT 1 FROM pg_roles WHERE rolname = 'transcendence_migrator'
          AND has_schema_privilege(oid, 'public', 'USAGE')
          AND has_schema_privilege(oid, 'public', 'CREATE')
    )
THEN 'ready' ELSE 'pending' END;
"""


class RolesBootstrapError(Exception):
    """Public errors never contain passwords, tokens or command output."""


def sanitized_run(argv, *, stdin=None, env=None, timeout=90,
                  accepted=(0,), pass_fds=()):
    try:
        result = subprocess.run(
            argv, input=stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=env, timeout=timeout, check=False, pass_fds=pass_fds,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RolesBootstrapError(f"Command unavailable or timed out: {argv[0]}") from exc
    if result.returncode not in accepted:
        raise RolesBootstrapError(
            f"Command failed: {argv[0]} (exit {result.returncode}; output withheld)"
        )
    return result


def safe_env():
    # Never inherit credentials accidentally from the operator's shell.
    keep = ("PATH", "HOME", "USER", "DOCKER_HOST", "DOCKER_CONTEXT",
            "DOCKER_CONFIG", "XDG_RUNTIME_DIR")
    env = {name: os.environ[name] for name in keep if name in os.environ}
    env["FT_DEV_TLS_DIR"] = str(config.vault.STATE / "tls")
    env["FT_APP_COMPOSE_PROJECT"] = pg.PROJECT
    env["FT_EXISTING_RUNTIME_ONLY"] = "1"
    return env


def app_compose(*args):
    return ["docker", "compose", "--env-file", "/dev/null", "-p",
            pg.PROJECT, "-f", str(pg.COMPOSE_FILE), *args]


def approle_inventory():
    """Probe only presence/readability of two files, never their contents.

    Uses an ephemeral networkless container, read-only named-volume mount.
    Return 2 for neither file, 3 for a partial/unsafe state.
    """
    probe = (
        "if [ -s /vault/auth/role_id ] && [ -r /vault/auth/role_id ] && "
        "[ -s /vault/auth/secret_id ] && [ -r /vault/auth/secret_id ]; "
        "then exit 0; fi; "
        "if [ ! -e /vault/auth/role_id ] && [ ! -e /vault/auth/secret_id ]; "
        "then exit 2; fi; exit 3"
    )
    result = sanitized_run([
        "docker", "run", "--rm", "--network", "none", "--read-only",
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
        "--user", "100:1000", "--mount",
        f"type=volume,source={ROLE_VOLUME},target=/vault/auth,readonly",
        "--entrypoint", "/bin/sh", VAULT_IMAGE, "-ec", probe,
    ], accepted=(0, 2, 3), timeout=60)
    if result.returncode == 0:
        return "present"
    if result.returncode == 2:
        return "missing"
    raise RolesBootstrapError(
        "PostgreSQL provisioner AppRole volume has partial/unsafe credentials"
    )


def role_state():
    state = pg.db_query(pg.NAME, ROLE_STATUS_SQL).strip()
    if state not in ("ready", "pending"):
        raise RolesBootstrapError("Unexpected PostgreSQL role inspection result")
    return state


def require_prerequisites():
    # Existing read-only checks also verify Compose provenance, Vault topology
    # and that both immutable Vault KV v2 database credentials exist.
    if pg.main(["check"]) != 0:
        raise RolesBootstrapError("PostgreSQL is not ready")
    if secret_bootstrap.main(["check"]) != 0:
        raise RolesBootstrapError("Vault PostgreSQL secrets are not ready")


def ensure_provisioner_approle(env):
    # Existing Compose service owns its AppRole volume. Never issue another
    # SecretID when the credential files already exist.
    sanitized_run(app_compose(
        "--profile", "vault-setup", "run", "--rm", "--no-deps", "-T",
        "vault-postgres-provisioner-storage-init",
    ), env=env, timeout=90)

    before = approle_inventory()
    if before == "present":
        print("KEEP: PostgreSQL provisioner AppRole credentials", flush=True)
        return

    # The existing helper reads the root token privately from stdin. It writes
    # role_id and secret_id into the private provisioner auth volume.
    token = config.recovery_token()
    sanitized_run(app_compose(
        "--profile", "vault-setup", "run", "--rm", "--no-deps", "-T",
        "vault-postgres-provisioner-provision",
    ), env=env, stdin=(token + "\n").encode(), timeout=120)
    if approle_inventory() != "present":
        raise RolesBootstrapError("PostgreSQL provisioner AppRole delivery incomplete")
    print("CREATED: PostgreSQL provisioner AppRole credentials", flush=True)


def run_existing_provisioner(env, lock_fd):
    # Parent holds the same inode and *open file description* inherited as fd9.
    # infra/vault/scripts/provision-postgres.sh checks /proc/self/fd/9, so it
    # shares the migration lock rather than deadlocking against its parent.
    if lock_fd != 9:
        raise RolesBootstrapError("Unexpected provisioning lock file descriptor")
    sanitized_run([
        "bash", str(ROOT / "infra/vault/scripts/provision-postgres.sh"),
    ], env=env, timeout=600, pass_fds=(9,))
    print("PASS: Existing Vault-to-PostgreSQL SCRAM provisioning workflow", flush=True)


def apply_roles():
    env = safe_env()
    with pg.LOCK.open("rb") as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RolesBootstrapError(
                "Another provisioning or migration workflow is running"
            ) from exc
        # Duplicate the *already locked* open file descriptor into 9, as used
        # by the existing migration/provisioning scripts.
        prior = None
        try:
            try:
                prior = os.dup(9)
            except OSError:
                pass
            os.dup2(lock_file.fileno(), 9, inheritable=True)
            ensure_provisioner_approle(env)
            run_existing_provisioner(env, 9)
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
        # `plan` is read-only and never asks for or reads Vault credentials.
        if args.mode == "plan":
            if pg.main(["plan"]) != 0:
                raise RolesBootstrapError("PostgreSQL container inventory failed")
            item = pg.exact_container(pg.NAME)
            if item is None or not (item.get("State") or {}).get("Running"):
                print("TODO: PostgreSQL roles not inspectable (read-only)")
            else:
                pg.verify_identity(item)
                if role_state() == "ready":
                    print("EXISTS: PostgreSQL application and migrator roles (read-only)")
                else:
                    print("TODO: PostgreSQL roles require provisioning (read-only)")
            print("RESULT: POSTGRESQL ROLES PLAN COMPLETE (read-only)")
            return 0

        require_prerequisites()
        if args.mode == "check":
            if role_state() != "ready":
                raise RolesBootstrapError("PostgreSQL role state or grants are incomplete")
            print("PASS: PostgreSQL SCRAM roles and schema privileges (metadata only)")
            print("RESULT: POSTGRESQL ROLES READY (read-only)")
            return 0

        apply_roles()
        if role_state() != "ready":
            raise RolesBootstrapError("PostgreSQL role provisioning verification failed")
        print("PASS: PostgreSQL SCRAM roles and schema privileges")
        print("RESULT: POSTGRESQL ROLES READY (apply)")
        return 0
    except (RolesBootstrapError, pg.PostgresBootstrapError,
            config.ConfigError, config.vault.VaultBootstrapError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        print("RESULT: POSTGRESQL ROLES STOPPED (no volume removal)", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
