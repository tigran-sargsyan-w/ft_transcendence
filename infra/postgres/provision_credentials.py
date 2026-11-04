#!/usr/bin/env python3
"""Reconcile PostgreSQL role credentials with Vault."""

import argparse
import secrets
import subprocess
from contextlib import contextmanager

from credential_contract import parse_database_url
from provision_roles import (
    ROLES,
    inspect_role,
    provision_missing_role,
    verify_password,
)


COMPOSE = [
    "docker",
    "compose",
    "-f", "compose.yml",
    "-f", "compose.vault.yml",
]


@contextmanager
def credential_reader_container():
    """Start a temporary container without logging credentials."""

    name = "ft-pg-reader-" + secrets.token_hex(8)
    started = False

    try:
        command = COMPOSE + [
            "--profile", "vault-maintenance",
            "run",
            "--detach",
            "--rm",
            "--no-deps",
            "-T",
            "--name", name,
            "vault-postgres-provisioner-reader",
        ]

        result = subprocess.run(
            command,
            capture_output=True,
            check=False,
            timeout=60,
        )

        if result.returncode != 0:
            raise RuntimeError(
                "Failed to start isolated credential reader"
            )

        started = True
        yield name

    finally:
        result = subprocess.run(
            ["docker", "rm", "-f", "-v", name],
            capture_output=True,
            check=False,
            timeout=30,
        )

        if started and result.returncode != 0:
            raise RuntimeError(
                f"Failed to remove credential reader: {name}"
            )


def read_vault_credential(
    purpose: str,
    username: str,
    container: str,
):
    """Read a Vault-rendered DSN through docker exec."""

    if purpose not in ROLES or ROLES[purpose] != username:
        raise ValueError("Unexpected credential purpose or role")

    filename = f"{purpose}_database_url"

    command = [
        "docker", "exec",
        container,
        "/bin/cat",
        f"/vault/rendered/{filename}",
    ]

    result = subprocess.run(
        command,
        capture_output=True,
        check=False,
        timeout=30,
    )

    if result.returncode != 0:
        raise RuntimeError(
            f"{purpose}: Vault credential delivery unavailable"
        )

    try:
        database_url = result.stdout.decode("utf-8")
    except UnicodeError:
        raise RuntimeError(
            f"{purpose}: Invalid credential encoding"
        ) from None

    return parse_database_url(database_url, username)


def verify_reader_logs(container: str) -> None:
    """Ensure no output was recorded in the reader container logs."""

    result = subprocess.run(
        ["docker", "logs", container],
        capture_output=True,
        check=False,
        timeout=30,
    )

    if result.returncode != 0:
        raise RuntimeError(
            "Could not inspect credential reader Docker logs"
        )

    if result.stdout or result.stderr:
        raise RuntimeError(
            "Credential reader unexpectedly emitted Docker logs"
        )

    print("PASS: Credential reader Docker logs are empty")


def reconcile_credentials(apply_new: bool) -> None:
    with credential_reader_container() as container:
        for purpose, username in ROLES.items():
            credential = read_vault_credential(
                purpose,
                username,
                container,
            )

            can_login, has_password = inspect_role(username)

            if can_login and has_password:
                if not verify_password(
                    username,
                    credential.password,
                ):
                    raise RuntimeError(
                        f"{purpose}: Vault and PostgreSQL "
                        "credentials mismatch"
                    )

                print(f"PASS: {purpose} credentials verified")
                continue

            if not can_login and not has_password:
                if not apply_new:
                    raise RuntimeError(
                        f"{purpose}: Initial provisioning required"
                    )

                result = provision_missing_role(
                    username,
                    credential.password,
                )

                if result != "created":
                    raise RuntimeError(
                        f"{purpose}: Unexpected provisioning result"
                    )

                print(f"PASS: {purpose} role provisioned")
                continue

            raise RuntimeError(
                f"{purpose}: Unsupported PostgreSQL role state"
            )

        verify_reader_logs(container)

    print("PASS: PostgreSQL credentials reconciliation completed")


def main() -> None:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--apply-new",
        action="store_true",
        help="Initialize roles that have NOLOGIN and no password",
    )

    args = parser.parse_args()
    reconcile_credentials(apply_new=args.apply_new)


if __name__ == "__main__":
    main()
