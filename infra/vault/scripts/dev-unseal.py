#!/usr/bin/env python3
"""Development-only host-assisted Vault unseal tooling (issue #107)."""

import argparse
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

    args = parser.parse_args(argv)

    if args.command == "doctor":
        return doctor(args.tls_dir)

    return 2


if __name__ == "__main__":
    sys.exit(main())
