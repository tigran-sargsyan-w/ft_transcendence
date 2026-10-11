
import argparse
import os
import platform
import re
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_DIR = Path(__file__).resolve().parent

DEFAULT_DOMAIN = "transcendence.test"


def is_wsl() -> bool:
    return (
        platform.system() == "Linux"
        and "microsoft" in platform.release().lower()
    )


def validate_domain(value: str) -> str:
    domain = value.strip().lower()

    if domain == "localhost":
        return domain

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
        raise ValueError("Invalid local .test domain")

    return domain


def run_linux(action: str, domain: str) -> int:
    print("[DOMAIN] Linux hosts", flush=True)

    # Unified cleanup is preview-only for now.
    if action == "cleanup":
        linux_action = "cleanup-plan"
    elif action == "cleanup-apply":
        linux_action = "cleanup"
    else:
        linux_action = action

    command = [
        sys.executable,
        "-m",
        "scripts.dev.linux_hosts",
        linux_action,
        "--domain",
        domain,
    ]

    return subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        check=False,
    ).returncode


def run_windows(action: str, domain: str) -> int:
    print("[DOMAIN] Windows hosts", flush=True)

    windows_action = (
        "cleanup"
        if action == "cleanup-apply"
        else action
    )

    script = SCRIPT_DIR / "windows_hosts.ps1"

    if is_wsl():
        result = subprocess.run(
            ["wslpath", "-w", str(script)],
            capture_output=True,
            text=True,
            check=True,
        )
        script_path = result.stdout.strip()
    else:
        script_path = str(script)

    command = [
        "powershell.exe",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        script_path,
        "-Action",
        windows_action,
        "-Domain",
        domain,
    ]

    if action in ("setup", "cleanup-apply"):
        command.append("-Apply")

    return subprocess.run(
        command,
        check=False,
    ).returncode


def manage(action: str, domain: str) -> int:
    domain = validate_domain(domain)

    if domain == "localhost":
        print("[OK] localhost requires no hosts modification")
        return 0

    system = platform.system()

    print(f"[SYSTEM] {system}")
    print(f"[WSL] {'yes' if is_wsl() else 'no'}")
    print(f"[DOMAIN] {domain}", flush=True)

    if system == "Linux":
        adapters = [run_linux]

        if is_wsl():
            adapters.append(run_windows)

    elif system == "Windows":
        adapters = [run_windows]

    else:
        print(f"[ERROR] Unsupported OS adapter: {system}")
        return 2

    results = []


    results = []

    for adapter in adapters:
        result = adapter(action, domain)
        results.append(result)

        # Stop immediately if a modifying operation fails.
        if action in ("setup", "cleanup-apply") and result != 0:
            return result

    if 2 in results:
        return 2

    if 1 in results:
        return 1

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Manage local development domain"
    )

    parser.add_argument(
        "action",
        choices=["check", "setup", "cleanup"],
    )

    parser.add_argument(
        "--domain",
        default=os.environ.get(
            "DEV_DOMAIN",
            DEFAULT_DOMAIN,
        ),
    )

    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually remove managed hosts entries during cleanup",
    )

    args = parser.parse_args()

    if args.apply and args.action != "cleanup":
        parser.error("--apply is supported only with cleanup")

    effective_action = (
        "cleanup-apply"
        if args.action == "cleanup" and args.apply
        else args.action
    )

    try:
        return manage(effective_action, args.domain)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f"[ERROR] {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
