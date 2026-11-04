#!/usr/bin/env python3
"""Integration test against an isolated PostgreSQL 18 container."""

import secrets
import subprocess
import time
from pathlib import Path

import provision_roles as roles


ROOT = Path(__file__).resolve().parents[2]
IMAGE = "transcendence-postgres:latest"
NAME = "ft-pg-probe-" + secrets.token_hex(6)

ADMIN = "ft_probe_admin"
DATABASE = "transcendence"


def docker(*args, input_text=None, timeout=60):
    return subprocess.run(
        ["docker", *args],
        input=input_text,
        text=True,
        capture_output=True,
        check=False,
        timeout=timeout,
    )


def admin_sql(sql):
    result = docker(
        "exec", "-i", NAME,
        "psql",
        "-X", "-A", "-t",
        "-v", "ON_ERROR_STOP=1",
        "-U", ADMIN,
        "-d", DATABASE,
        input_text=sql,
    )

    if result.returncode != 0:
        raise RuntimeError(
            "SQL execution failed in isolated PostgreSQL"
        )

    return result.stdout.strip()


def tcp_auth(username, password):
    command = (
        "IFS= read -r PGPASSWORD; "
        "export PGPASSWORD; "
        'exec psql -X -w -h 127.0.0.1 -p 5432 '
        '-U "$1" -d "$2" -Atqc "SELECT 1"'
    )

    result = docker(
        "exec", "-i", NAME,
        "sh", "-ec",
        command,
        "credential-check",
        username,
        DATABASE,
        input_text=password + "\n",
        timeout=20,
    )

    return (
        result.returncode == 0
        and result.stdout.strip() == "1"
    )


def run_test():
    print("=== Step 1: Start isolated PostgreSQL 18 ===")

    if docker("image", "inspect", IMAGE).returncode != 0:
        raise RuntimeError(
            "Project PostgreSQL image not found. "
            "Build the postgres service first."
        )

    result = docker(
        "run", "-d", "--rm",
        "--name", NAME,
        "--network", "none",
        "-e", f"POSTGRES_USER={ADMIN}",
        "-e", f"POSTGRES_DB={DATABASE}",
        "-e", "POSTGRES_INITDB_ARGS=--auth-host=scram-sha-256",
        "-e", "POSTGRES_PASSWORD=" + secrets.token_urlsafe(32),
        IMAGE,
    )

    if result.returncode != 0:
        raise RuntimeError(
            "Failed to create isolated PostgreSQL container"
        )

    print("PASS: Isolated container started")

    print("=== Step 2: Wait for PostgreSQL readiness ===")

    for _ in range(60):
        ready = docker(
            "exec", NAME,
            "pg_isready", "-q",
            "-U", ADMIN,
            "-d", DATABASE,
        )

        if ready.returncode == 0:
            break

        time.sleep(1)
    else:
        raise RuntimeError("Isolated PostgreSQL did not become ready")

    print("PASS: PostgreSQL ready")

    print("=== Step 3: Execute real ensure-roles.sql ===")

    sql = (
        ROOT / "infra/postgres/ensure-roles.sql"
    ).read_text(encoding="utf-8")

    admin_sql(sql)

    # Redirect the real provisioner to the isolated database.
    # Neither PostgreSQL operations nor authentication are mocked.
    roles.postgres_admin = admin_sql
    roles.verify_password = tcp_auth

    for username in roles.ROLES.values():
        state = roles.inspect_role(username)

        if state != (False, False):
            raise RuntimeError(
                f"{username}: Expected initial NOLOGIN state"
            )

    print("PASS: Both roles created with NOLOGIN and no password")

    print("=== Step 4: Provision both roles ===")

    passwords = {}

    for purpose, username in roles.ROLES.items():
        password = secrets.token_urlsafe(32)
        passwords[username] = password

        result = roles.provision_missing_role(
            username, password
        )

        if result != "created":
            raise RuntimeError(
                f"{purpose}: Initial provisioning failed"
            )

        if roles.inspect_role(username) != (True, True):
            raise RuntimeError(
                f"{purpose}: LOGIN/password not configured"
            )

        if not tcp_auth(username, password):
            raise RuntimeError(
                f"{purpose}: Correct password rejected"
            )

        if tcp_auth(username, "wrong-" + password):
            raise RuntimeError(
                f"{purpose}: Incorrect password accepted"
            )

        print(f"PASS: {purpose} role provisioned via SCRAM")

    print("=== Step 5: Verify idempotency ===")

    admin_sql(sql)

    for username, password in passwords.items():
        result = roles.provision_missing_role(
            username, password
        )

        if result != "existing":
            raise RuntimeError(
                f"{username}: Existing role was not preserved"
            )

        if not tcp_auth(username, password):
            raise RuntimeError(
                f"{username}: Password no longer works"
            )

    print("PASS: Repeated provisioning preserves existing roles")
    print("PASS: Fresh PostgreSQL provisioning integration test")


try:
    run_test()
finally:
    result = docker("rm", "-f", "-v", NAME)

    if result.returncode == 0:
        print("PASS: Isolated container and volumes removed")
    else:
        print(
            "NOTICE: Check whether temporary container "
            f"{NAME} needs manual cleanup"
        )
