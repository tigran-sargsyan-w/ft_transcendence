#!/usr/bin/env python3
"""Trusted Grafana secret provisioning with create-once semantics."""

import argparse
import os
import subprocess
import sys
from pathlib import Path

import bootstrap_vault as vault
import bootstrap_vault_config as config

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "scripts/dev/grafana_secrets_helper.py"
CA_FILE = vault.STATE / "tls/ca.crt"
NETWORK = "transcendence-vault-dev_vault_private"
IMAGE = "python:3.12-alpine"
MARKER = "FT_GRAFANA_SECRETS_RESULT="


class GrafanaSecretsError(Exception):
    """Safe diagnostics without credential values."""


class GrafanaSecretsMissing(GrafanaSecretsError):
    """Only the validated helper missing-state permits initial provisioning."""


def helper_command(mode):
    if mode not in ("check", "apply"):
        raise GrafanaSecretsError("Unexpected helper mode")

    for path in (HELPER, CA_FILE):
        if not path.is_file() or path.is_symlink():
            raise GrafanaSecretsError(
                "Required Grafana bootstrap file missing or unsafe"
            )

    return [
        "docker", "run", "--rm", "--interactive",
        "--read-only",
        "--network", NETWORK,
        "--user", "65534:65534",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges:true",
        "--pids-limit", "128",
        "--mount", f"type=bind,src={HELPER},dst=/bootstrap/helper.py,readonly",
        "--mount", f"type=bind,src={CA_FILE},dst=/vault/tls/ca.crt,readonly",
        "--env", "PYTHONDONTWRITEBYTECODE=1",
        IMAGE, "python3", "-B", "/bootstrap/helper.py", mode,
    ]


def parse_result(output):
    try:
        lines = output.decode("utf-8").splitlines()

        results = [
            line[len(MARKER):]
            for line in lines
            if line.startswith(MARKER)
        ]

        if len(results) != 1:
            raise ValueError

        if results[0] not in ("created", "preserved", "missing"):
            raise ValueError

        return results[0]

    except (ValueError, UnicodeError, TypeError):
        raise GrafanaSecretsError(
            "Invalid Grafana helper result"
        ) from None


def run_helper(mode, token):
    safe_env = {
        key: value
        for key, value in os.environ.items()
        if key in (
            "PATH", "HOME", "USER", "DOCKER_HOST",
            "DOCKER_CONTEXT", "DOCKER_CONFIG", "XDG_RUNTIME_DIR",
        )
    }

    try:
        result = subprocess.run(
            helper_command(mode),
            input=(token + "\n").encode("utf-8"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=safe_env,
            check=False,
            timeout=30 if mode == "check" else 180,
        )

    except (OSError, subprocess.TimeoutExpired):
        raise GrafanaSecretsError(
            "Grafana secret helper unavailable or timed out"
        ) from None

    if result.returncode not in (0, 2):
        raise GrafanaSecretsError(
            f"Grafana secrets {mode} failed; raw output withheld"
        )

    state = parse_result(result.stdout)

    if result.returncode == 2:
        if mode != "check" or state != "missing":
            raise GrafanaSecretsError("Unexpected helper exit status")

        raise GrafanaSecretsMissing("Required Grafana secrets are missing")

    if state == "missing":
        raise GrafanaSecretsError("Helper reported success with missing secrets")

    if mode == "check" and state != "preserved":
        raise GrafanaSecretsError("Read-only helper returned a write status")

    print(f"{state.upper()}: Grafana Vault credentials", flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("plan", "check", "check-missing", "apply"))
    args = parser.parse_args(argv)

    try:
        if args.mode == "plan":
            print("INFO: Grafana secrets use create-once KV v2 provisioning")
            print("RESULT: GRAFANA SECRET PLAN COMPLETE (read-only)")
            return 0

        if config.main(["check"]) != 0:
            raise GrafanaSecretsError("Trusted Vault configuration not ready")

        token = config.recovery_token()

        run_helper("check" if args.mode == "check-missing" else args.mode, token)

        if args.mode == "apply":
            run_helper("check", token)

        print(f"RESULT: GRAFANA VAULT SECRETS READY ({args.mode})")
        return 0

    except GrafanaSecretsMissing as exc:
        if args.mode == "check-missing":
            print("MISSING: Initial Grafana Vault credentials", flush=True)
            return 2
        print(f"ERROR: {exc}", file=sys.stderr)
        print("RESULT: GRAFANA SECRET PROVISIONING STOPPED", file=sys.stderr)
        return 1
    except (
        GrafanaSecretsError,
        config.ConfigError,
        vault.VaultBootstrapError,
    ) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        print(
            "RESULT: GRAFANA SECRET PROVISIONING STOPPED",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
