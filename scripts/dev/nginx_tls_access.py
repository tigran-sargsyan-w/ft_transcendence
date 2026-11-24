#!/usr/bin/env python3
"""Provision tightly scoped, host-local ACL for the unprivileged WAF gateway.

The host group grants only search on the cert directory and read on the
certificate pair. Docker Compose attaches GID 43043 solely to nginx.

check and plan never modify host state. apply is operator-initiated and
idempotent; it never removes existing ACLs or rewrites private keys.
"""

import argparse
import grp
import os
import pwd
from pathlib import Path
import stat
import subprocess
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CERT_DIR = PROJECT_ROOT / "infra/nginx/certs"
CERT = CERT_DIR / "cert.pem"
KEY = CERT_DIR / "key.pem"
GROUP_NAME = "ft-transcendence-waf-tls"
GROUP_GID = 43043
DESIRED = {CERT_DIR: "--x", CERT: "r--", KEY: "r--"}


class TLSAccessError(Exception):
    """No private file content is included in public error messages."""


def checked_command(argv):
    try:
        result = subprocess.run(
            argv, check=False, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise TLSAccessError(f"Required ACL command unavailable: {argv[0]}") from None
    if result.returncode:
        raise TLSAccessError(f"ACL operation failed: {argv[0]}")
    return result.stdout


def check_files():
    # Disallow symlinks in the repository-relative path to the key.
    for path in (PROJECT_ROOT / "infra", PROJECT_ROOT / "infra/nginx", CERT_DIR, CERT, KEY):
        try:
            info = path.lstat()
        except OSError:
            raise TLSAccessError("Expected Nginx TLS path is missing") from None
        if stat.S_ISLNK(info.st_mode):
            raise TLSAccessError("Refusing symlink in Nginx TLS path")
    for path in (CERT_DIR, CERT, KEY):
        info = path.stat()
        if info.st_uid != os.getuid():
            raise TLSAccessError("Unexpected TLS owner; refusing to modify ACL")
        if path != CERT_DIR and info.st_nlink != 1:
            raise TLSAccessError("Unexpected TLS file hard links")
        if stat.S_IMODE(info.st_mode) & 0o007 and path != CERT:
            raise TLSAccessError("TLS key/directory has world-access permissions")
    if not stat.S_ISDIR(CERT_DIR.stat().st_mode):
        raise TLSAccessError("TLS directory is not a directory")
    if not all(stat.S_ISREG(p.stat().st_mode) for p in (CERT, KEY)):
        raise TLSAccessError("TLS certificate/key must be regular files")
    if not stat.S_IMODE(CERT_DIR.stat().st_mode) & stat.S_IXUSR:
        raise TLSAccessError("TLS directory owner lacks execute permission")
    if not stat.S_IMODE(KEY.stat().st_mode) & stat.S_IRUSR:
        raise TLSAccessError("TLS key owner lacks read permission")


def group_state():
    """Return true for exact existing name/GID, false when both are free."""
    try:
        by_name = grp.getgrnam(GROUP_NAME)
    except KeyError:
        by_name = None
    try:
        by_id = grp.getgrgid(GROUP_GID)
    except KeyError:
        by_id = None
    if by_name is None and by_id is None:
        return False
    if (by_name is None or by_id is None
            or by_name.gr_gid != GROUP_GID or by_id.gr_name != GROUP_NAME):
        raise TLSAccessError("Reserved TLS group name/GID is occupied unexpectedly")
    if by_name.gr_mem or any(user.pw_gid == GROUP_GID for user in pwd.getpwall()):
        raise TLSAccessError("Dedicated TLS group must have no host user members")
    return True


def acl_entries(path):
    raw = checked_command(["getfacl", "-n", "-c", "-p", str(path)])
    entries = [line.strip() for line in raw.splitlines() if line.strip()]
    for line in entries:
        if line.startswith("default:") or line.startswith("user:") and not line.startswith("user::"):
            raise TLSAccessError("Unexpected named user/default TLS ACL; manual review required")
        if line.startswith("group:") and not line.startswith("group::"):
            if not line.startswith(f"group:{GROUP_GID}:"):
                raise TLSAccessError("Unexpected named group in TLS ACL")
    return entries


def acl_ready():
    result = True
    for path, perms in DESIRED.items():
        entries = acl_entries(path)
        if f"group:{GROUP_GID}:{perms}" not in entries:
            # Different permissions for this group must be reviewed, not silently overwritten.
            if any(x.startswith(f"group:{GROUP_GID}:") for x in entries):
                raise TLSAccessError("TLS group ACL has unexpected permissions")
            result = False
        if f"group:{GROUP_GID}:{perms}" in entries and f"mask::{perms}" not in entries:
            raise TLSAccessError("TLS ACL group mask does not permit required access")
        if path in (CERT_DIR, KEY) and "other::---" not in entries:
            raise TLSAccessError("TLS directory/key has unexpected public ACL")
        if path == KEY and "group::---" not in entries:
            raise TLSAccessError("TLS private key has unexpected owning-group ACL")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("plan", "check", "apply"))
    args = parser.parse_args(argv)
    try:
        check_files()
        exists = group_state()
        ready = acl_ready()
        if args.mode == "plan":
            print("READY" if exists and ready else "TODO", "Nginx dedicated TLS group and ACL (read-only)")
            return 0
        if args.mode == "check":
            if not (exists and ready):
                raise TLSAccessError("Dedicated TLS group/ACL not configured")
            print("PASS: Dedicated Nginx TLS group and restrictive ACL (read-only)")
            return 0
        if os.geteuid() == 0:
            raise TLSAccessError("Run as the ordinary project owner, not root")
        if exists and ready:
            print("KEEP: Dedicated Nginx TLS group and ACL already configured")
            return 0
        if not exists:
            # This is intentionally the only privileged operation.
            # In --prepare-host this follows explicit operator confirmation.
            try:
                subprocess.run(
                    ["sudo", "groupadd", "--system", "--gid", str(GROUP_GID), GROUP_NAME],
                    check=True, timeout=60,
                )
            except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
                raise TLSAccessError("Could not create dedicated TLS group") from None
            if not group_state():
                raise TLSAccessError("Dedicated TLS group not visible after creation")
        for path, perms in DESIRED.items():
            entries = acl_entries(path)
            if f"group:{GROUP_GID}:{perms}" not in entries:
                checked_command(["setfacl", "-m", f"g:{GROUP_GID}:{perms}", str(path)])
        if not acl_ready():
            raise TLSAccessError("TLS group ACL verification failed after apply")
        print("PASS: Dedicated Nginx TLS group/ACL configured")
        return 0
    except TLSAccessError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
