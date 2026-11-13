#!/usr/bin/env python3
"""Fresh Ubuntu Vault server bootstrap (development only).

Modes:
  plan  : read-only inventory
  check : verify an initialized, unsealed Vault with the expected topology
  apply : reuse or create only the declared Vault container; initialize only a
          genuinely uninitialized Vault, persist recovery material outside Git
          with private permissions before unsealing with two initial shares.

Never sends credentials as command arguments, writes secrets to logs, or
reinitializes an existing Vault. Never uses Docker Compose down or -v.
"""

import argparse
import json
import os
import stat
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROJECT = "transcendence-vault-dev"
NAME = PROJECT + "-vault-1"
COMPOSE = ROOT / "infra/vault/server/compose.yml"
STATE = Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state"))) / "transcendence/dev"
RECOVERY = STATE / "private/vault-init.json"


class VaultBootstrapError(Exception):
    """Error messages never include subprocess output or secret values."""


def call(args, *, input_data=None, accepted=(0,), timeout=90, env=None):
    try:
        result = subprocess.run(
            args, input=input_data, capture_output=True, timeout=timeout, check=False, env=env,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise VaultBootstrapError(f"Command unavailable or timed out: {args[0]}") from exc
    if result.returncode not in accepted:
        raise VaultBootstrapError(
            f"Command failed: {args[0]} (exit {result.returncode}); no secret output shown"
        )
    return result.stdout


def json_result(command, **kwargs):
    try:
        return json.loads(call(command, **kwargs))
    except (ValueError, TypeError, UnicodeError) as exc:
        raise VaultBootstrapError("Invalid JSON from Docker/Vault") from exc


def inspect_container():
    # Docker 29 can return `[]` when inspecting a missing container.
    # Detect absence by exact container name instead of accepting inspect
    # failures, which could conceal an unrelated Docker error.
    names = call([
        "docker", "container", "ls", "--all",
        "--filter", f"name=^/{NAME}$",
        "--format", "{{.Names}}",
    ], timeout=25).decode("utf-8").splitlines()
    if not names:
        return None
    if names != [NAME]:
        raise VaultBootstrapError("Unexpected Vault container inventory")

    output = call(["docker", "container", "inspect", NAME], timeout=25)
    try:
        items = json.loads(output)
    except (TypeError, ValueError):
        raise VaultBootstrapError("Invalid Docker inspect JSON") from None
    if not isinstance(items, list) or len(items) != 1:
        raise VaultBootstrapError("Unexpected Vault container inspection result")
    return items[0]


def verify_identity(item):
    labels = (item.get("Config") or {}).get("Labels") or {}
    if (
        item.get("Name") != "/" + NAME
        or labels.get("com.docker.compose.project") != PROJECT
        or labels.get("com.docker.compose.service") != "vault"
        or labels.get("com.docker.compose.project.config_files") != str(COMPOSE)
        or labels.get("com.docker.compose.project.working_dir") != str(COMPOSE.parent)
    ):
        raise VaultBootstrapError("Unexpected Vault container identity/Compose provenance")
    mounts = item.get("Mounts") or []
    data = [m for m in mounts if m.get("Destination") == "/vault/data"]
    tls = [m for m in mounts if m.get("Destination") == "/vault/tls"]
    expected_tls = STATE / "tls"
    if (
        len(data) != 1 or data[0].get("Name") != PROJECT + "_vault_data"
        or data[0].get("Type") != "volume"
        or len(tls) != 1 or tls[0].get("Type") != "bind"
        or tls[0].get("Source") != str(expected_tls)
        or tls[0].get("RW") is not False
    ):
        raise VaultBootstrapError("Unexpected Vault storage/TLS mounts; refusing to touch container")
    networks = (item.get("NetworkSettings") or {}).get("Networks") or {}
    if set(networks) != {PROJECT + "_vault_private"}:
        raise VaultBootstrapError("Unexpected Vault network attachment")
    return item


def status(tries=15):
    last = None
    for attempt in range(tries):
        try:
            state = json_result(
                ["docker", "exec", NAME, "vault", "status", "-format=json"],
                accepted=(0, 2), timeout=10,
            )
            if isinstance(state, dict) and type(state.get("initialized")) is bool and type(state.get("sealed")) is bool:
                return state
            last = "Vault status incomplete"
        except VaultBootstrapError:
            last = "Vault status unavailable"
        if attempt + 1 < tries:
            time.sleep(2)
    raise VaultBootstrapError(last or "Vault status unavailable")


def verify_topology(state, *, require_ready):
    if state.get("storage_type") != "raft":
        raise VaultBootstrapError("Vault storage is not Raft")
    if state.get("seal_type") not in (None, "shamir"):
        raise VaultBootstrapError("Unexpected Vault seal type")
    if state.get("initialized"):
        if (state.get("n"), state.get("t")) != (3, 2):
            raise VaultBootstrapError("Unexpected Vault Shamir configuration")
        if require_ready and state.get("sealed") is not False:
            raise VaultBootstrapError("Vault is sealed: use ./scripts/dev-start.sh for manual unseal")
    elif require_ready:
        raise VaultBootstrapError("Vault is not initialized")


def validate_secret_path():
    if ROOT == STATE or ROOT in STATE.parents:
        raise VaultBootstrapError("Secret state must not reside inside repository")
    # The host project intentionally uses the standard XDG state path.
    if not STATE.is_absolute():
        raise VaultBootstrapError("XDG state must resolve to an absolute path")
    for p in (STATE, STATE / "private", RECOVERY):
        if p.is_symlink():
            raise VaultBootstrapError("Symlink found in private recovery path")
    if RECOVERY.exists():
        s = RECOVERY.stat()
        if not stat.S_ISREG(s.st_mode) or s.st_uid != os.getuid() or (s.st_mode & 0o077):
            raise VaultBootstrapError("Recovery file has unsafe ownership/permissions")
    return RECOVERY


def ensure_private_dir():
    validate_secret_path()
    directory = RECOVERY.parent
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if directory.is_symlink():
        raise VaultBootstrapError("Private recovery directory must not be a symlink")
    s = directory.stat()
    if s.st_uid != os.getuid() or not stat.S_ISDIR(s.st_mode) or (s.st_mode & 0o077):
        raise VaultBootstrapError("Private recovery directory is not owned by user with mode 0700")
    validate_secret_path()


def initialize_vault():
    # Reserve the private destination *before* calling operator init, to avoid
    # initializing Vault when no safe output destination exists.
    ensure_private_dir()
    if RECOVERY.exists():
        raise VaultBootstrapError(
            "Protected recovery file exists but Vault is uninitialized: STOP (possible lost volume)"
        )
    try:
        fp = RECOVERY.open("xb")
    except OSError as exc:
        raise VaultBootstrapError("Cannot reserve private recovery file") from exc
    with fp:
        os.fchmod(fp.fileno(), 0o600)
        try:
            raw = call([
                "docker", "exec", NAME,
                "vault", "operator", "init", "-key-shares=3", "-key-threshold=2", "-format=json",
            ], timeout=45)
            # Persist the exact raw recovery output BEFORE parsing it. If parsing
            # fails after a successful Vault init, the original secrets still
            # exist privately and can be recovered by the trusted operator.
            fp.write(raw)
            fp.flush()
            os.fsync(fp.fileno())
            try:
                payload = json.loads(raw)
            except (TypeError, ValueError, UnicodeError) as exc:
                raise VaultBootstrapError(
                    "Vault init response malformed; private raw recovery output preserved"
                ) from exc
            shares = payload.get("unseal_keys_b64")
            token = payload.get("root_token")
            if (not isinstance(shares, list) or len(shares) != 3
                    or not all(isinstance(s, str) and s for s in shares)
                    or not isinstance(token, str) or not token):
                raise VaultBootstrapError(
                    "Vault init response incomplete; private raw recovery output preserved"
                )
        except Exception:
            # Leave the reserved file in place on uncertainty. Never attempt
            # automatic re-init after an ambiguous Vault initialization.
            raise
    # Sync the directory metadata so the recovery file survives sudden loss.
    dir_fd = os.open(str(RECOVERY.parent), os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)

    print("PASS: Vault initialized (Shamir 2-of-3)", flush=True)
    print(f"IMPORTANT: Recovery material stored privately at: {RECOVERY}", flush=True)
    print("SECURITY: Contains root token and all three shares; protect it outside the VM.", flush=True)
    print("NEVER paste or commit this file. It is NOT safe for a shared/production Vault.", flush=True)

    for index, share in enumerate(shares[:2], 1):
        call([
            "docker", "exec", "-i", NAME, "vault", "write", "sys/unseal", "-",
        ], input_data=json.dumps({"key": share}).encode("utf-8"), timeout=15)
        current = status(tries=3)
        if index == 1 and (current.get("sealed") is not True or current.get("progress") != 1):
            raise VaultBootstrapError("Unexpected first Shamir unseal response")
        if index == 2 and current.get("sealed") is not False:
            raise VaultBootstrapError("Unexpected second Shamir unseal response")

    verify_topology(status(), require_ready=True)
    print("PASS: Vault initialized, unsealed and using Raft", flush=True)


def start_or_keep():
    item = inspect_container()
    if item is not None:
        verify_identity(item)
        if (item.get("State") or {}).get("Running"):
            print(f"KEEP: {NAME}", flush=True)
            return
        if (item.get("State") or {}).get("Status") not in ("created", "exited"):
            raise VaultBootstrapError("Vault container in unexpected state")
        call(["docker", "start", NAME], timeout=55)
        print(f"START: {NAME}", flush=True)
    else:
        # The permissions helper is only launched by Compose dependency handling.
        # No other app containers are created in this phase.
        env = os.environ.copy()
        env["FT_DEV_TLS_DIR"] = str(STATE / "tls")
        call([
            "docker", "compose", "--env-file", "/dev/null", "-p", PROJECT,
            "-f", str(COMPOSE), "up", "-d", "--no-recreate", "vault",
        ], timeout=360, env=env)
        item = inspect_container()
        if item is None:
            raise VaultBootstrapError("Vault container not created")
        verify_identity(item)
        if not (item.get("State") or {}).get("Running"):
            raise VaultBootstrapError("Vault container not running")
        print(f"CREATED: {NAME}", flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("plan", "check", "apply"))
    args = parser.parse_args(argv)
    try:
        validate_secret_path()
        item = inspect_container()
        if args.mode == "plan":
            if item is None:
                print("TODO: Vault container not created (read-only)")
                if RECOVERY.exists():
                    print("WARNING: Recovery material exists but Vault container is missing")
            else:
                verify_identity(item)
                print(f"EXISTS: Vault container ({(item.get('State') or {}).get('Status')})")
                if (item.get("State") or {}).get("Running"):
                    v = status(tries=1)
                    verify_topology(v, require_ready=False)
                    print(f"Vault initialized: {v['initialized']}; sealed: {v['sealed']}")
            print("RESULT: VAULT PLAN COMPLETE (read-only)")
            return 0

        if args.mode == "check":
            if item is None or not (item.get("State") or {}).get("Running"):
                raise VaultBootstrapError("Vault container is not running")
            verify_identity(item)
            verify_topology(status(), require_ready=True)
            print("RESULT: VAULT READY (read-only)")
            return 0

        # Apply only on an explicitly authorized, fresh VM. The top-level
        # bootstrap.sh enforces host + Compose resources preflights and asks y/N.
        start_or_keep()
        v = status(tries=30)
        verify_topology(v, require_ready=False)
        if v["initialized"]:
            print("KEEP: Vault is already initialized; never reinitializing", flush=True)
            verify_topology(v, require_ready=True)
            print("RESULT: VAULT READY (reused existing state)")
            return 0
        initialize_vault()
        print("RESULT: VAULT READY (new development state)")
        return 0
    except VaultBootstrapError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        print("RESULT: VAULT BOOTSTRAP STOPPED (no resources removed)", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
