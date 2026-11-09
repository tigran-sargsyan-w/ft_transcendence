#!/usr/bin/env python3
"""Operator-triggered AppRole SecretID cutover, with explicit finalization.

Runs in a short-lived, role-scoped Compose container on vault_private.
Only the role-specific auth volume is mounted read/write; no Docker socket.
No administrative credential or SecretID is accepted on the command line.
"""

from __future__ import annotations

import fcntl
import getpass
import hashlib
import hmac
import json
import os
from pathlib import Path
import ssl
import stat
import sys
import urllib.error
import urllib.request


ROLE_PATHS = {
    "backend-reader": ("backend-readonly", ("backend",)),
    "migrator-reader": ("migrator-readonly", ("migrator",)),
    "postgres-provisioner-reader": (
        "postgres-provisioner-readonly", ("backend", "migrator")
    ),
}
URL = "https://vault:8200"
CA = "/vault/tls/ca.crt"
STATE = ".rotation-pending.json"


class RotationError(Exception):
    """A deliberately sanitized error; never contains Vault response bodies."""


class MissingSecretIDLookup(RotationError):
    """SecretID lookup returned no data; absence needs confirmation."""


class VaultHTTPError(RotationError):
    """Vault HTTP status without untrusted error body or secret values."""

    def __init__(self, status: int, path: str):
        super().__init__(f"Vault request failed: HTTP {status} ({path})")
        self.status = status


class VaultAPI:
    def __init__(self, admin_token: str, url: str = URL, ca: str = CA):
        self.admin_token = admin_token
        self.url = url.rstrip("/")
        self.context = ssl.create_default_context(cafile=ca)

    def request(self, method: str, path: str, data=None, token=None):
        payload = None if data is None else json.dumps(data).encode("utf-8")
        request = urllib.request.Request(
            f"{self.url}/v1/{path}",
            data=payload,
            headers={
                "X-Vault-Token": self.admin_token if token is None else token,
                "Content-Type": "application/json",
            },
            method=method,
        )
        try:
            with urllib.request.urlopen(request, context=self.context, timeout=10) as resp:
                content = resp.read()
                return json.loads(content) if content else {}
        except urllib.error.HTTPError as exc:
            # Never print a Vault error body: it can echo request data.
            raise VaultHTTPError(exc.code, path) from None
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            raise RotationError(f"Vault request failed ({path}): {type(exc).__name__}") from None

    def role_id(self, role):
        return self.request("GET", f"auth/approle/role/{role}/role-id")["data"]["role_id"]

    def lookup(self, role, secret):
        path = f"auth/approle/role/{role}/secret-id/lookup"
        response = self.request(
            "POST", path, {"secret_id": secret}
        )

        data = response.get("data") if isinstance(response, dict) else None

        if data is None:
            raise MissingSecretIDLookup(
                "SecretID lookup returned no data"
            )

        if not isinstance(data, dict):
            raise RotationError("Unexpected SecretID lookup response")

        accessor = data.get("secret_id_accessor")

        if not isinstance(accessor, str) or not accessor:
            raise RotationError("SecretID lookup omitted accessor")

        return accessor

    def list_accessors(self, role):
        path = f"auth/approle/role/{role}/secret-id?list=true"

        try:
            response = self.request("GET", path)
        except VaultHTTPError as exc:
            if exc.status != 404:
                raise

            # A missing role must not be confused with an empty list.
            self.role_id(role)
            return []

        data = response.get("data") if isinstance(response, dict) else None
        keys = data.get("keys") if isinstance(data, dict) else None

        if not isinstance(keys, list) or not all(
            isinstance(key, str) and key for key in keys
        ):
            raise RotationError(
                "Unexpected SecretID accessor list response"
            )

        return keys

    def issue(self, role):
        data = self.request("POST", f"auth/approle/role/{role}/secret-id", {})["data"]
        return data["secret_id"], data["secret_id_accessor"]

    def destroy(self, role, accessor):
        self.request(
            "POST", f"auth/approle/role/{role}/secret-id-accessor/destroy",
            {"secret_id_accessor": accessor},
        )

    def destroy_if_present(self, role, accessor):
        try:
            self.destroy(role, accessor)
            return
        except VaultHTTPError as exc:
            if exc.status not in (400, 404):
                raise
        # An interruption after successful revocation can leave the journal.
        # Confirm the credential really no longer exists before retry succeeds.
        try:
            self.request(
                "POST", f"auth/approle/role/{role}/secret-id-accessor/lookup",
                {"secret_id_accessor": accessor},
            )
        except VaultHTTPError as exc:
            if exc.status in (400, 404):
                return
            raise
        raise RotationError("SecretID revocation was not confirmed")

    def verify(self, role, role_id, secret):
        expected_policy, paths = ROLE_PATHS[role]
        response = self.request(
            "POST", "auth/approle/login", {"role_id": role_id, "secret_id": secret}
        )
        auth = response.get("auth") or {}
        token = auth.get("client_token")
        if not token:
            raise RotationError("AppRole login returned no token")
        try:
            policies = set(auth.get("token_policies") or [])
            if policies != {expected_policy}:
                raise RotationError("AppRole token policies differ from expected least privilege")
            for name in paths:
                data = self.request(
                    "GET", f"kv/data/ft-transcendence/dev/{name}", token=token
                )
                if not (data.get("data") or {}).get("data", {}).get("database_url"):
                    raise RotationError(f"Required KV secret is not accessible: {name}")
        finally:
            # AppRole tokens deliberately have no default policy, hence no
            # revoke-self permission. The operator revokes the validation
            # token without widening the runtime/migration/reader ACLs.
            self.request("POST", "auth/token/revoke", {"token": token})


def _read(path: Path):
    if not path.exists():
        return None
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "r", encoding="utf-8") as stream:
        return stream.read().strip()


def _write(path: Path, value: str):
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o400)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        # Persist the rename before advancing the rotation transaction.
        dirfd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(dirfd)
        finally:
            os.close(dirfd)
    finally:
        temporary.unlink(missing_ok=True)


def _fingerprint(secret: str | None):
    # SecretIDs have high entropy; this digest is only for cutover detection.
    return hashlib.sha256(secret.encode("utf-8")).hexdigest() if secret else None


def _state(auth_dir: Path):
    raw = _read(auth_dir / STATE)
    if not raw:
        return None
    try:
        state = json.loads(raw)
        if set(state) != {"role", "old_accessor", "new_accessor", "old_fingerprint"}:
            raise ValueError("state fields")
        if not isinstance(state["new_accessor"], str) or not state["new_accessor"]:
            raise ValueError("missing accessor")
        if state["old_accessor"] is not None and not isinstance(state["old_accessor"], str):
            raise ValueError("old accessor type")
        if not isinstance(state["role"], str):
            raise ValueError("role type")
        fp = state["old_fingerprint"]
        if fp is not None and (not isinstance(fp, str) or len(fp) != 64
                            or any(c not in "0123456789abcdef" for c in fp)):
            raise ValueError("fingerprint type")
        return state
    except ValueError:
        raise RotationError("Invalid pending rotation state; inspect offline, do not overwrite") from None


def _clear(auth_dir: Path):
    (auth_dir / STATE).unlink(missing_ok=True)


def rotate(api: VaultAPI, role: str, auth_dir: Path, recover: bool = False):
    if _state(auth_dir) is not None:
        raise RotationError("Pending rotation exists; use finalize or abort first")

    live_role_id = api.role_id(role)
    previous_role_id = _read(auth_dir / "role_id")
    if previous_role_id and previous_role_id != live_role_id:
        raise RotationError("Stored RoleID differs from Vault; manual investigation required")

    previous_secret = _read(auth_dir / "secret_id")
    old_accessor = None
    if previous_secret:
        try:
            old_accessor = api.lookup(role, previous_secret)
        except (VaultHTTPError, MissingSecretIDLookup) as exc:
            # Never treat authorization, network or server failures
            # as proof that the old SecretID is missing.
            if isinstance(exc, VaultHTTPError) and exc.status not in (400, 404):
                raise

            stored_accessor = _read(auth_dir / "secret_id_accessor")
            active_accessors = api.list_accessors(role)

            if stored_accessor and stored_accessor in active_accessors:
                raise RotationError(
                    "Previous SecretID accessor is still active; refusing recovery"
                ) from None

            # Unknown active credentials require an operator audit.
            if active_accessors:
                raise RotationError(
                    "Other active SecretIDs prevent safe recovery"
                ) from None

            if not recover:
                raise RotationError(
                    "Old SecretID invalid or expired; use explicit recover mode"
                ) from None

            print(
                "WARNING: old SecretID is invalid/expired; "
                "audit previous accessors manually"
            )
    elif _read(auth_dir / "secret_id_accessor"):
        raise RotationError("Orphaned accessor with missing SecretID; manual inspection required")

    new_secret, new_accessor = api.issue(role)
    # Record the new accessor before any file cutover. If interrupted, a teammate
    # can revoke the unused candidate or finalize the installed one.
    try:
        _write(auth_dir / STATE, json.dumps({
            "role": role, "old_accessor": old_accessor, "new_accessor": new_accessor,
            "old_fingerprint": _fingerprint(previous_secret),
        }))
    except OSError:
        # Without a durable journal, don't leave an untracked live candidate.
        try:
            api.destroy(role, new_accessor)
        except RotationError:
            raise RotationError("Journal write failed AND candidate revocation failed; audit Vault") from None
        raise RotationError("Journal write failed; unused candidate revoked") from None
    print("Candidate SecretID issued; verifying its scoped login and KV access")
    api.verify(role, live_role_id, new_secret)
    # Each os.replace is atomic; the journal makes a multi-file interruption recoverable.
    _write(auth_dir / "role_id", live_role_id)
    _write(auth_dir / "secret_id", new_secret)
    _write(auth_dir / "secret_id_accessor", new_accessor)
    print("New credential installed. OLD SecretID has NOT been revoked.")
    print("Verify the corresponding real Agent workflow, then run: finalize")


def finalize(api: VaultAPI, role: str, auth_dir: Path):
    pending = _state(auth_dir)
    if not pending or pending["role"] != role:
        raise RotationError("No matching pending rotation to finalize")
    current_secret = _read(auth_dir / "secret_id")
    current_role_id = _read(auth_dir / "role_id")
    if not current_secret or not current_role_id:
        raise RotationError("Current credentials missing; cannot finalize")
    accessor = api.lookup(role, current_secret)
    if accessor != pending["new_accessor"]:
        raise RotationError("New SecretID not installed; use abort if it was never installed")
    api.verify(role, current_role_id, current_secret)
    old = pending["old_accessor"]
    if old:
        if old == accessor:
            raise RotationError("Refusing to revoke the active SecretID")
        api.destroy_if_present(role, old)
    _write(auth_dir / "secret_id_accessor", accessor)
    _clear(auth_dir)
    print("Rotation finalized; prior identifiable SecretID revoked")
    if not old:
        print("NOTE: no prior accessor was available; audit older credentials if recovering")


def abort(api: VaultAPI, role: str, auth_dir: Path):
    pending = _state(auth_dir)
    if not pending or pending["role"] != role:
        raise RotationError("No matching pending rotation to abort")
    current_secret = _read(auth_dir / "secret_id")
    expected = pending["old_fingerprint"]
    actual = _fingerprint(current_secret)
    if actual is None and expected is None:
        pass  # Initial provisioning interrupted before cutover.
    elif actual is None or expected is None or not hmac.compare_digest(actual, expected):
        # The installed credential changed (or its identity cannot be proved).
        # Never revoke a candidate which may be in use.
        raise RotationError("Auth file changed since staging; candidate may be installed; use finalize or operator recovery")
    api.destroy_if_present(role, pending["new_accessor"])
    _clear(auth_dir)
    print("Unused candidate revoked; previous credential untouched")


def operate(api: VaultAPI, role: str, auth_dir: Path, action: str):
    if role not in ROLE_PATHS:
        raise RotationError("Unsupported AppRole")
    if action not in {"rotate", "recover", "finalize", "abort"}:
        raise RotationError("Unsupported action")
    if auth_dir.is_symlink() or not auth_dir.is_dir():
        raise RotationError("Auth volume missing or symbolic link")
    perms = stat.S_IMODE(auth_dir.stat().st_mode)
    if perms & 0o077:
        raise RotationError("Auth directory must not be accessible to group/others")
    if not os.access(auth_dir, os.W_OK):
        raise RotationError("Auth directory must be writable by this role-scoped operator")
    # This lock only coordinates the new utility. Do not invoke legacy provisioning
    # services concurrently during migration to this workflow.
    lockfd = os.open(auth_dir / ".rotation.lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(lockfd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RotationError("Another rotation is running for this AppRole") from None
        if action == "finalize":
            finalize(api, role, auth_dir)
        elif action == "abort":
            abort(api, role, auth_dir)
        else:
            rotate(api, role, auth_dir, recover=(action == "recover"))
    finally:
        os.close(lockfd)


def main(argv):
    if len(argv) != 4:
        print("Usage: rotate-approle.py ROLE AUTH_DIR {rotate|recover|finalize|abort}", file=sys.stderr)
        return 2
    role, auth_dir, action = argv[1:]
    if role not in ROLE_PATHS or action not in {"rotate", "recover", "finalize", "abort"}:
        print("Unsupported role or action", file=sys.stderr)
        return 2
    os.umask(0o077)
    try:
        admin_token = getpass.getpass("Vault operator token (hidden): ")
        if not admin_token:
            raise RotationError("Empty operator token")
        api = VaultAPI(admin_token)
        api.request("GET", "auth/token/lookup-self")
        operate(api, role, Path(auth_dir), action)
        return 0
    except (RotationError, OSError, KeyError, TypeError, EOFError) as exc:
        # Never include a raw secret, HTTP body, or Vault token in errors.
        message = str(exc) if isinstance(exc, RotationError) else type(exc).__name__
        print(f"ERROR: {message}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
