#!/usr/bin/env python3
"""PostgreSQL role inspection and credential verification."""

import subprocess
from pathlib import Path

from credential_contract import parse_database_url
from scram import make_scram_verifier


COMPOSE = [
    "docker", "compose",
    "-f", "compose.yml",
    "-f", "compose.vault.yml",
]

ROLES = {
    "backend": "transcendence_app",
    "migrator": "transcendence_migrator",
}


def postgres_admin(sql: str) -> str:
    """Execute administrative SQL without passing it as an argument."""

    command = COMPOSE + [
        "exec", "-T", "postgres",
        "sh", "-c",
        'exec psql -X -A -t -v ON_ERROR_STOP=1 '
        '-U "$POSTGRES_USER" -d "$POSTGRES_DB"',
    ]

    result = subprocess.run(
        command,
        input=sql,
        text=True,
        capture_output=True,
        check=False,
    )

    if result.returncode != 0:
        raise RuntimeError("PostgreSQL administrative query failed")

    return result.stdout.strip()


def inspect_role(username: str) -> tuple[bool, bool]:
    """Return LOGIN status and whether a password verifier exists."""

    if username not in ROLES.values():
        raise ValueError("Unexpected PostgreSQL role")

    sql = f"""
        SELECT
            rolcanlogin::int || ':' ||
            (rolpassword IS NOT NULL)::int
        FROM pg_authid
        WHERE rolname = '{username}';
    """

    result = postgres_admin(sql)

    if result not in ("0:0", "0:1", "1:0", "1:1"):
        raise RuntimeError("PostgreSQL role is missing or invalid")

    login, password_exists = result.split(":")

    return login == "1", password_exists == "1"


def verify_password(username: str, password: str) -> bool:
    """Check real TCP/SCRAM authentication, not Unix socket trust."""

    if username not in ROLES.values():
        raise ValueError("Unexpected PostgreSQL role")

    command = COMPOSE + [
        "exec", "-T", "postgres",
        "sh", "-ec",
        'IFS= read -r PGPASSWORD; '
        'export PGPASSWORD; '
        'exec psql -X -w '
        '-h postgres -p 5432 '
        '-U "$1" -d transcendence '
        '-Atqc "SELECT 1"',
        "credential-check",
        username,
    ]

    result = subprocess.run(
        command,
        input=password + "\n",
        text=True,
        capture_output=True,
        check=False,
    )

    return result.returncode == 0 and result.stdout.strip() == "1"


def provision_missing_role(username: str, password: str) -> str:
    """Activate only an uninitialized PostgreSQL role.

    Existing LOGIN roles are never modified.
    """

    if username not in ROLES.values():
        raise ValueError("Unexpected PostgreSQL role")

    can_login, has_password = inspect_role(username)

    if can_login and has_password:
        return "existing"

    if can_login or has_password:
        raise RuntimeError(
            "PostgreSQL role is in an unexpected state"
        )

    verifier = make_scram_verifier(password)

    # Username is restricted to our fixed allowlist.
    # Verifier is generated internally, never accepted as SQL input.
    sql = f"""
BEGIN;

SELECT pg_advisory_xact_lock(
    hashtext('ft_transcendence_postgres_roles')
);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_authid
        WHERE rolname = '{username}'
          AND NOT rolcanlogin
          AND rolpassword IS NULL
    ) THEN
        RAISE EXCEPTION
            'Role state changed during provisioning';
    END IF;
END
$$;

ALTER ROLE {username}
    WITH LOGIN PASSWORD '{verifier}';

COMMIT;
"""

    postgres_admin(sql)

    if not verify_password(username, password):
        raise RuntimeError(
            "PostgreSQL authentication failed after provisioning"
        )

    return "created"


def inspect_credentials(
    backend_file: Path,
    migrator_file: Path,
) -> None:
    """Inspect roles without changing credentials or database objects."""

    for purpose, path in (
        ("backend", backend_file),
        ("migrator", migrator_file),
    ):
        username = ROLES[purpose]

        credential = parse_database_url(
            path.read_text(encoding="utf-8"),
            username,
        )

        can_login, has_password = inspect_role(username)

        if can_login and has_password:
            if not verify_password(username, credential.password):
                raise RuntimeError(
                    f"{purpose}: Vault credentials do not match PostgreSQL"
                )

            print(f"PASS: {purpose} credentials verified")
            continue

        if not can_login and not has_password:
            print(f"PENDING: {purpose} role requires initial provisioning")
            continue

        raise RuntimeError(
            f"{purpose}: unexpected PostgreSQL role state"
        )


if __name__ == "__main__":
    print(
        "PostgreSQL credential inspection module loaded. "
        "No database changes performed."
    )
