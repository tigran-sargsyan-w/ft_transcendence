#!/usr/bin/env python3
"""Configure/verify Vault KV v2, ACLs and AppRoles after Vault initialization.

The temporary Compose helper receives the operator token over stdin only:
never via argv, Docker environment, logs, or repository files.
PostgreSQL credentials/DSNs are NOT created in this phase.
"""

import argparse
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import bootstrap_vault as vault


class ConfigError(Exception):
    """Only safe diagnostic strings can be displayed."""


def recovery_token():
    # The recovery file must have been created by our Phase 3 initializer.
    vault.validate_secret_path()
    path = vault.RECOVERY
    if not path.is_file():
        raise ConfigError("Protected Vault recovery file is missing")
    parent = path.parent
    mode = parent.stat().st_mode
    if (parent.is_symlink() or parent.stat().st_uid != os.getuid()
            or not stat.S_ISDIR(mode) or (mode & 0o077)):
        raise ConfigError("Vault recovery directory ownership/permissions are unsafe")
    mode = path.stat().st_mode
    if (not stat.S_ISREG(mode) or path.stat().st_uid != os.getuid()
            or (mode & 0o077)):
        raise ConfigError("Vault recovery file ownership/permissions are unsafe")
    try:
        data = json.loads(path.read_bytes())
    except (OSError, ValueError, UnicodeError):
        raise ConfigError("Vault recovery file is unreadable or malformed") from None
    if not isinstance(data, dict) or not isinstance(data.get("root_token"), str):
        raise ConfigError("Vault recovery file has no root token")
    token = data["root_token"]
    if not token or any(c in token for c in "\r\n\x00"):
        raise ConfigError("Invalid recovery token format")
    return token


def helper_command(mode):
    if mode not in ("apply", "check"):
        raise ValueError("Unexpected Vault configuration mode")
    return [
        "docker", "compose", "--env-file", "/dev/null", "-p", vault.PROJECT,
        "-f", str(vault.COMPOSE), "--profile", "setup",
        "run", "--rm", "--no-deps", "-T",
        "--env", f"FT_BOOTSTRAP_MODE={mode}", "vault-bootstrap",
    ]


def run_helper(mode, token):
    env = {key: val for key, val in os.environ.items() if key in (
        "PATH", "HOME", "USER", "DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CONFIG", "XDG_RUNTIME_DIR"
    )}
    env["FT_DEV_TLS_DIR"] = str(vault.STATE / "tls")
    try:
        result = subprocess.run(
            helper_command(mode), input=(token + "\n").encode("utf-8"),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=180,
            check=False, env=env,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise ConfigError("Vault config helper unavailable or timed out") from None
    if result.returncode:
        # Intentionally never print the helper's raw stdout/stderr, which may
        # include sensitive troubleshooting details from Docker/Vault.
        raise ConfigError(f"Vault config {mode} failed (exit {result.returncode}); inspect trusted operator logs locally")
    print(f"PASS: Vault configuration {mode} completed (KV v2, ACLs, three AppRoles)", flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("plan", "check", "apply"))
    args = parser.parse_args(argv)
    try:
        # Reuse the strict Phase 3 container identity and Raft readiness check.
        if vault.main(["check"]) != 0:
            raise ConfigError("Trusted Vault is not ready")
        if args.mode == "plan":
            print("INFO: Vault config status requires authenticated check; no mutation attempted")
            print("RESULT: VAULT CONFIG PLAN COMPLETE (read-only)")
            return 0
        token = recovery_token()
        run_helper(args.mode, token)
        if args.mode == "apply":
            # Verify resulting state using an explicit read-only check.
            run_helper("check", token)
        print(f"RESULT: VAULT CONFIG READY ({args.mode})")
        return 0
    except (ConfigError, vault.VaultBootstrapError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        print("RESULT: VAULT CONFIG STOPPED (no resources removed)", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
