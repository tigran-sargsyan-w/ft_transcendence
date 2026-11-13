#!/usr/bin/env python3
"""Phase 4B: configure two independent PostgreSQL DSNs in Vault KV v2.

Never sends Vault tokens or database passwords through command-line arguments,
container environment, logs, or host files. The ephemeral helper generates
passwords in memory and writes missing secrets with KV v2 CAS=0.
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import bootstrap_vault as vault
import bootstrap_vault_config as config

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "scripts/dev/vault_secrets_helper.py"
CONTRACT = ROOT / "infra/postgres/credential_contract.py"
CA_FILE = vault.STATE / "tls/ca.crt"
NETWORK = "transcendence-vault-dev_vault_private"
IMAGE = "python:3.12-alpine"
MARKER = "FT_VAULT_DATABASE_SECRETS_RESULT="
PURPOSES = ("backend", "migrator")


class SecretsBootstrapError(Exception):
    """Safe, credential-free messages only."""


def helper_command(mode):
    if mode not in ("check", "apply"):
        raise ValueError("Unexpected database secrets mode")
    for path in (HELPER, CONTRACT, CA_FILE):
        if not path.is_file() or path.is_symlink():
            raise SecretsBootstrapError("A required local bootstrap file is missing or unsafe")
    # --mount refuses missing sources; --read-only and the internal network
    # prevent the transient helper from leaving files or reaching other networks.
    return [
        "docker", "run", "--rm", "--interactive", "--read-only",
        "--network", NETWORK, "--user", "65534:65534", "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges:true", "--pids-limit", "128",
        "--mount", f"type=bind,src={HELPER},dst=/bootstrap/helper.py,readonly",
        "--mount", f"type=bind,src={CONTRACT},dst=/bootstrap/credential_contract.py,readonly",
        "--mount", f"type=bind,src={CA_FILE},dst=/vault/tls/ca.crt,readonly",
        "--env", "PYTHONDONTWRITEBYTECODE=1",
        IMAGE, "python3", "-B", "/bootstrap/helper.py", mode,
    ]


def parse_result(output):
    # Docker may print pull/progress lines. Only accept a *single* typed status
    # emitted by our helper; raw output is never forwarded to the terminal.
    try:
        lines = output.decode("utf-8").splitlines()
        matches = [line[len(MARKER):] for line in lines if line.startswith(MARKER)]
        if len(matches) != 1:
            raise ValueError("Missing or repeated helper result")
        data = json.loads(matches[0])
        if not isinstance(data, dict) or set(data) != set(PURPOSES):
            raise ValueError("Unexpected helper fields")
        if not all(data[key] in ("created", "preserved", "missing") for key in PURPOSES):
            raise ValueError("Unexpected helper status")
        return data
    except (ValueError, UnicodeError, TypeError):
        raise SecretsBootstrapError("Invalid or missing private helper status") from None


def run_helper(mode, root_token):
    safe_env = {key: val for key, val in os.environ.items()
                if key in ("PATH", "HOME", "USER", "DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CONFIG", "XDG_RUNTIME_DIR")}
    try:
        result = subprocess.run(
            helper_command(mode), input=(root_token + "\n").encode("utf-8"),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=safe_env,
            check=False, timeout=180,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise SecretsBootstrapError("Database secrets helper unavailable or timed out") from None

    if result.returncode not in (0, 2):
        raise SecretsBootstrapError(
            f"Database secrets {mode} failed (exit {result.returncode}); raw output hidden"
        )
    states = parse_result(result.stdout)
    if result.returncode == 2 and (mode != "check" or "missing" not in states.values()):
        raise SecretsBootstrapError("Unexpected database secrets helper exit status")
    if result.returncode == 0 and "missing" in states.values():
        raise SecretsBootstrapError("Helper claimed success with missing secrets")
    if mode == "check" and any(state == "created" for state in states.values()):
        raise SecretsBootstrapError("Read-only helper returned a write status")
    for purpose, state in states.items():
        print(f"{state.upper()}: Vault {purpose} database credential", flush=True)
    if result.returncode:
        raise SecretsBootstrapError("One or more Vault database credentials are missing")
    print(f"PASS: Vault database credentials {mode} verified", flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("plan", "check", "apply"))
    args = parser.parse_args(argv)
    try:
        if args.mode == "plan":
            print("INFO: PostgreSQL Vault secret values require authenticated inspection; no secrets read")
            print("RESULT: DATABASE SECRETS PLAN COMPLETE (read-only)")
            return 0
        if config.main(["check"]) != 0:
            raise SecretsBootstrapError("Trusted Vault configuration not ready")
        token = config.recovery_token()
        run_helper(args.mode, token)
        if args.mode == "apply":
            run_helper("check", token)
        print(f"RESULT: VAULT DATABASE SECRETS READY ({args.mode})")
        return 0
    except (SecretsBootstrapError, config.ConfigError, vault.VaultBootstrapError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        print("RESULT: VAULT DATABASE SECRETS STOPPED (existing secrets never replaced)", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
