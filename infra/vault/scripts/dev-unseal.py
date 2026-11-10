#!/usr/bin/env python3
"""Development-only host-assisted Vault unseal tooling (issue #107)."""

import argparse
import getpass
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_TLS_DIR = ROOT / "infra/vault/server/tls"


def probe(command, timeout=8):
    """Query local state without printing command output."""
    try:
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def successful(result):
    return result is not None and result.returncode == 0


def doctor(tls_dir):
    failures = 0

    def report(level, name, detail):
        nonlocal failures
        print(f"{level:<4} {name}: {detail}")
        if level == "FAIL":
            failures += 1

    report(
        "PASS" if sys.platform == "linux" else "FAIL",
        "Platform",
        sys.platform,
    )

    version = f"{sys.version_info.major}.{sys.version_info.minor}"
    report(
        "PASS" if sys.version_info >= (3, 10) else "FAIL",
        "Python",
        version,
    )

    for name in ("docker", "secret-tool", "busctl", "openssl"):
        available = shutil.which(name) is not None
        report(
            "PASS" if available else "FAIL",
            name,
            "available" if available else "missing",
        )

    if shutil.which("docker"):
        checks = (
            ("Docker Compose", ["docker", "compose", "version"]),
            (
                "Docker Engine",
                ["docker", "info", "--format", "{{.ServerVersion}}"],
            ),
        )

        for label, command in checks:
            result = probe(command)
            report(
                "PASS" if successful(result) else "FAIL",
                label,
                "accessible" if successful(result) else "unavailable",
            )

    if shutil.which("busctl"):
        alias = probe([
            "busctl",
            "--user",
            "call",
            "org.freedesktop.secrets",
            "/org/freedesktop/secrets",
            "org.freedesktop.Secret.Service",
            "ReadAlias",
            "s",
            "default",
        ])

        if not successful(alias):
            report("FAIL", "Secret Service", "unavailable")
        else:
            report("PASS", "Secret Service", "reachable")

            try:
                parts = shlex.split(alias.stdout.strip())
                collection = (
                    parts[1]
                    if len(parts) == 2 and parts[0] == "o"
                    else "/"
                )
            except ValueError:
                collection = "/"

            if collection == "/":
                report(
                    "WARN",
                    "Default Keyring",
                    "not configured; run setup",
                )
            elif collection == "/org/freedesktop/secrets/collection/session":
                report(
                    "WARN",
                    "Default Keyring",
                    "temporary session collection; persistent Keyring required",
                )
            else:
                status = probe([
                    "busctl",
                    "--user",
                    "get-property",
                    "org.freedesktop.secrets",
                    collection,
                    "org.freedesktop.Secret.Collection",
                    "Locked",
                ])

                if not successful(status):
                    report(
                        "FAIL",
                        "Default Keyring",
                        "cannot read lock status",
                    )
                elif status.stdout.strip() == "b true":
                    report(
                        "WARN",
                        "Default Keyring",
                        "locked; unlock before setup/unseal",
                    )
                elif status.stdout.strip() == "b false":
                    report(
                        "PASS",
                        "Default Keyring",
                        "unlocked",
                    )
                else:
                    report(
                        "FAIL",
                        "Default Keyring",
                        "invalid lock status",
                    )

    ca_file = tls_dir / "ca.crt"

    if ca_file.is_file() and not ca_file.is_symlink():
        report("PASS", "Vault CA", "certificate file exists")
    else:
        report(
            "WARN",
            "Vault CA",
            "not configured; see Vault server setup",
        )

    print(
        "RESULT:",
        "NOT READY" if failures else "CHECKS COMPLETE",
    )
    return 1 if failures else 0



def setup_keyring():
    """Create or unlock a persistent GNOME login Keyring."""
    if sys.platform != "linux":
        print("FAIL: Linux environment required")
        return 1

    for dependency in ("busctl", "gnome-keyring-daemon", "secret-tool"):
        if shutil.which(dependency) is None:
            print(f"FAIL: Missing dependency: {dependency}")
            return 1

    owner = probe([
        "busctl", "--user", "status", "org.freedesktop.secrets",
    ])

    if not successful(owner) or "gnome-keyring-daemon" not in owner.stdout:
        print("FAIL: GNOME Keyring Secret Service provider not confirmed")
        return 1

    def keyring_state():
        alias = probe([
            "busctl", "--user", "call",
            "org.freedesktop.secrets",
            "/org/freedesktop/secrets",
            "org.freedesktop.Secret.Service",
            "ReadAlias", "s", "default",
        ])

        if not successful(alias):
            return None, None

        try:
            parts = shlex.split(alias.stdout.strip())
        except ValueError:
            return None, None

        if len(parts) != 2 or parts[0] != "o":
            return None, None

        collection = parts[1]

        if collection == "/" or collection.endswith("/session"):
            return collection, None

        if collection != "/org/freedesktop/secrets/collection/login":
            return collection, None

        status = probe([
            "busctl", "--user", "get-property",
            "org.freedesktop.secrets",
            collection,
            "org.freedesktop.Secret.Collection",
            "Locked",
        ])

        if not successful(status):
            return collection, None

        value = status.stdout.strip()

        if value == "b false":
            return collection, False
        if value == "b true":
            return collection, True

        return collection, None

    collection, locked = keyring_state()
    login = "/org/freedesktop/secrets/collection/login"

    if collection is None:
        print("FAIL: Cannot inspect Secret Service")
        return 1

    if collection == login and locked is False:
        print("PASS: Persistent login Keyring is already unlocked")
        return 0

    if collection not in ("/", "/org/freedesktop/secrets/collection/session", login):
        print("FAIL: Unexpected default collection; refusing to replace it")
        return 1

    creating = collection != login

    if creating:
        print("INFO: Persistent login Keyring needs to be created")
    elif locked is True:
        print("INFO: Persistent login Keyring is locked")
    else:
        print("FAIL: Cannot determine login Keyring lock status")
        return 1

    print(
        "WARNING: Unlocking will replace the GNOME Keyring daemon "
        "and may interrupt other applications using it."
    )

    if input("Continue? [y/N]: ").strip().lower() not in ("y", "yes"):
        print("INFO: Setup cancelled")
        return 1

    password = getpass.getpass("Keyring password: ")

    if not password:
        print("FAIL: Empty Keyring password is not allowed")
        return 1

    if creating:
        confirmation = getpass.getpass("Confirm Keyring password: ")
        if password != confirmation:
            print("FAIL: Passwords do not match")
            return 1
        del confirmation

    try:
        result = subprocess.run(
            [
                "gnome-keyring-daemon",
                "--daemonize",
                "--replace",
                "--unlock",
                "--components=secrets",
            ],
            input=password,
            text=True,
            capture_output=True,
            check=False,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired):
        print("FAIL: Keyring operation failed")
        return 1
    finally:
        del password

    if result.returncode != 0:
        print("FAIL: GNOME Keyring could not be initialized")
        return 1

    collection, locked = keyring_state()

    if collection != login or locked is not False:
        print("FAIL: Persistent login Keyring is not unlocked")
        return 1

    keyring_dir = Path.home() / ".local/share/keyrings"
    keyring_file = keyring_dir / "login.keyring"

    if (
        not keyring_dir.is_dir()
        or keyring_dir.is_symlink()
        or not keyring_file.is_file()
        or keyring_file.is_symlink()
        or keyring_dir.stat().st_mode & 0o077
        or keyring_file.stat().st_mode & 0o077
    ):
        print("FAIL: Persistent Keyring files have unsafe permissions")
        return 1

    print("PASS: Persistent login Keyring is configured and unlocked")
    print("INFO: No Vault secrets were stored or modified")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tls-dir",
        type=Path,
        default=DEFAULT_TLS_DIR,
    )

    commands = parser.add_subparsers(
        dest="command",
        required=True,
    )
    commands.add_parser(
        "doctor",
        help="Read-only host readiness checks",
    )

    commands.add_parser(
        "setup",
        help="Prepare persistent GNOME Keyring (no Vault shares yet)",
    )

    commands.add_parser(
        "enroll",
        help="Opt in: store two dev-only Shamir shares in GNOME login Keyring",
    )
    commands.add_parser(
        "unseal",
        help="Unseal existing dev Vault through an ephemeral TLS client",
    )
    commands.add_parser(
        "forget",
        help="Remove host-enrolled development Vault shares from Keyring",
    )

    args = parser.parse_args(argv)

    if args.command == "doctor":
        return doctor(args.tls_dir)

    if args.command == "setup":
        return setup_keyring()

    if args.command in ("enroll", "unseal", "forget"):
        from dev_unseal_workflow import run_command
        return run_command(args.command, args.tls_dir)

    return 2


if __name__ == "__main__":
    sys.exit(main())
