
#!/usr/bin/env python3

import argparse
import fcntl
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from scripts.dev.hosts_editor import (
    HostsConflictError,
    ensure_mapping,
    remove_managed_mapping,
)

HOSTS_FILE = Path("/etc/hosts")
DEFAULT_DOMAIN = "transcendence.test"


def validate_domain(domain: str) -> str:
    domain = domain.strip().lower()

    labels = domain.split(".")

    if (
        len(domain) > 253
        or len(labels) < 2
        or labels[-1] != "test"
        or not all(
            re.fullmatch(
                r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?",
                label,
            )
            for label in labels
        )
    ):
        raise ValueError(
            "Domain must be a valid name ending in .test"
        )

    return domain


def append_text(content: str, domain: str) -> str:
    updated, changed = ensure_mapping(content, domain)
    return updated[len(content):] if changed else ""


def read_hosts() -> str:
    with HOSTS_FILE.open(
        "r", encoding="utf-8", newline=""
    ) as file:
        return file.read()


def check(domain: str) -> int:
    addition = append_text(read_hosts(), domain)

    if addition:
        print(f"[MISSING] {domain} is not configured")
        return 1

    print(f"[OK] {domain} -> 127.0.0.1")
    return 0


def apply_mapping(domain: str) -> int:
    if os.geteuid() != 0:
        print("[ERROR] Root privileges required")
        return 2

    # Lock the file and re-check its content after
    # acquiring privileges, to avoid stale decisions.
    with HOSTS_FILE.open(
        "r+", encoding="utf-8", newline=""
    ) as file:
        fcntl.flock(file.fileno(), fcntl.LOCK_EX)

        file.seek(0)
        current = file.read()
        addition = append_text(current, domain)

        if not addition:
            print("[OK] Domain already configured")
            return 0

        # Append only. Preserve existing hosts entries.
        file.seek(0, os.SEEK_END)
        file.write(addition)
        file.flush()
        os.fsync(file.fileno())

    print(f"[OK] Added {domain} -> 127.0.0.1")
    return 0


def setup(domain: str) -> int:
    addition = append_text(read_hosts(), domain)

    if not addition:
        print("[OK] Domain already configured")
        return 0

    print("[DOMAIN] Proposed change to /etc/hosts:")
    print(addition, end="")

    answer = input("Apply this change? [y/N]: ")

    if answer.strip().lower() not in ("y", "yes"):
        print("[SKIP] No changes made")
        return 1

    if os.geteuid() == 0:
        return apply_mapping(domain)

    if shutil.which("sudo") is None:
        print("[ERROR] sudo is unavailable")
        print("[INFO] Use localhost as a fallback")
        return 2

    return subprocess.run(
        [
            "sudo",
            sys.executable,
            "-m",
            "scripts.dev.linux_hosts",
            "_apply",
            "--domain",
            domain,
        ],
        check=False,
    ).returncode



def remove_mapping(domain: str) -> int:
    if os.geteuid() != 0:
        print("[ERROR] Root privileges required")
        return 2

    with HOSTS_FILE.open(
        "r+", encoding="utf-8", newline=""
    ) as file:
        fcntl.flock(file.fileno(), fcntl.LOCK_EX)

        try:
            file.seek(0)
            current = file.read()

            updated, changed = remove_managed_mapping(
                current, domain
            )

            if not changed:
                print("[OK] No managed entry to remove")
                return 0

            file.seek(0)
            file.write(updated)
            file.truncate()
            file.flush()
            os.fsync(file.fileno())

        finally:
            fcntl.flock(file.fileno(), fcntl.LOCK_UN)

    print(f"[OK] Removed managed mapping for {domain}")
    return 0


def cleanup(domain: str) -> int:
    current = read_hosts()

    _, changed = remove_managed_mapping(
        current, domain
    )

    if not changed:
        print("[OK] No managed entry to remove")
        return 0

    print("[DOMAIN] The following managed entry will be removed:")
    print(
        f"127.0.0.1\t{domain}\t"
        "# ft_transcendence:managed"
    )

    answer = input("Remove this entry? [y/N]: ")

    if answer.strip().lower() not in ("y", "yes"):
        print("[SKIP] No changes made")
        return 1

    if os.geteuid() == 0:
        return remove_mapping(domain)

    if shutil.which("sudo") is None:
        print("[ERROR] sudo is unavailable")
        return 2

    return subprocess.run(
        [
            "sudo",
            sys.executable,
            "-m",
            "scripts.dev.linux_hosts",
            "_remove",
            "--domain",
            domain,
        ],
        check=False,
    ).returncode


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Manage the Linux development domain"
    )

    parser.add_argument(
        "command",
        choices=[
            "check",
            "setup",
            "cleanup",
            "_apply",
            "_remove",
        ],
    )
    parser.add_argument(
        "--domain",
        default=os.environ.get("DEV_DOMAIN", DEFAULT_DOMAIN),
    )

    args = parser.parse_args()

    if not sys.platform.startswith("linux"):
        print("[ERROR] This module supports Linux only")
        return 2

    try:
        domain = validate_domain(args.domain)

        if args.command == "check":
            return check(domain)

        if args.command == "setup":
            return setup(domain)

        if args.command == "cleanup":
            return cleanup(domain)

        if args.command == "_apply":
            return apply_mapping(domain)

        return remove_mapping(domain)

    except (
        OSError,
        ValueError,
        HostsConflictError,
        EOFError,
    ) as error:
        print(f"[ERROR] {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
