#!/usr/bin/env python3
"""Reconcile PostgreSQL role credentials with Vault."""

import argparse
import subprocess

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


def read_vault_credential(purpose: str, username: str):
    """Read a rendered DSN without printing it to the terminal."""

    if purpose not in ROLES:
        raise ValueError("Unexpected credential purpose")

    filename = f"{purpose}_database_url"

    command = COMPOSE + [
        "--profile", "vault-maintenance",
        "run", "--rm", "--no-deps", "-T",
        "--entrypoint", "/bin/cat",
        "vault-postgres-provisioner-cleanup",
        f"/vault/rendered/{filename}",
    ]

    result = subprocess.run(
        command,
        capture_output=True,
        check=False,
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


def reconcile_credentials(apply_new: bool) -> None:
    for purpose, username in ROLES.items():
        credential = read_vault_credential(purpose, username)

        can_login, has_password = inspect_role(username)

        if can_login and has_password:
            if not verify_password(username, credential.password):
                raise RuntimeError(
                    f"{purpose}: Vault and PostgreSQL credentials mismatch"
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
