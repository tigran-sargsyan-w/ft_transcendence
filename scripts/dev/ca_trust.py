
"""Local CA trust management for ft_transcendence."""

import argparse
import platform
import re
import subprocess
from pathlib import Path

from scripts.dev import certificates, domains


def run(*args: str):
    return subprocess.run(
        list(args),
        capture_output=True,
        text=True,
        check=False,
    )


def get_thumbprint(ca_file: Path) -> str | None:
    """Get the SHA-1 identifier used by Windows certificate stores."""

    result = run(
        "openssl", "x509",
        "-in", str(ca_file),
        "-noout", "-fingerprint", "-sha1",
    )

    if result.returncode != 0:
        return None

    thumbprint = (
        result.stdout.split("=")[-1]
        .replace(":", "")
        .strip()
        .upper()
    )

    if not re.fullmatch(r"[0-9A-F]{40}", thumbprint):
        return None

    return thumbprint


def check_linux(ca_file: Path) -> bool:
    """Check OpenSSL system CA trust on Linux."""

    result = run(
        "openssl", "verify",
        "-CApath", "/etc/ssl/certs",
        str(ca_file),
    )

    if result.returncode == 0:
        print("[OK] Linux OpenSSL: CA trusted")
        return True

    print("[MISSING] Linux OpenSSL: CA not trusted")
    return False


def check_windows(thumbprint: str) -> bool | None:
    """Check Windows user and machine trusted root stores."""

    script = (
        "$ErrorActionPreference = 'Stop'; "
        f"$thumb = '{thumbprint}'; "
        "$stores = @("
        "'Cert:\\CurrentUser\\Root',"
        "'Cert:\\LocalMachine\\Root'); "
        "foreach ($store in $stores) { "
        "$found = @(Get-ChildItem -Path $store | "
        "Where-Object { $_.Thumbprint -eq $thumb }); "
        "if ($found.Count -gt 0) { "
        "Write-Output \"[OK] $store\" "
        "} else { "
        "Write-Output \"[MISSING] $store\" "
        "} "
        "}"
    )

    try:
        result = run(
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            script,
        )
    except OSError as error:
        print(f"[ERROR] Windows PowerShell: {error}")
        return None

    if result.returncode != 0:
        print("[ERROR] Windows trust check failed")
        print(result.stderr.strip())
        return None

    output = result.stdout.strip()
    print(output)

    if not output:
        return None

    return "[OK]" in output


def inspect(action: str) -> int:
    print("[TRUST] Local CA trust management")

    system = platform.system()
    print(f"[SYSTEM] {system}")

    ca_root = certificates.get_ca_root()

    if ca_root is None:
        return 2

    ca_file = ca_root / "rootCA.pem"

    if not ca_file.is_file():
        print("[MISSING] Root CA certificate")
        return 1

    thumbprint = get_thumbprint(ca_file)

    if thumbprint is None:
        print("[ERROR] Cannot identify Root CA")
        return 2

    print(f"[CA] {ca_file}")
    print(f"[THUMBPRINT] {thumbprint}")

    missing = False

    if system == "Linux":
        linux_trusted = check_linux(ca_file)

        if not linux_trusted:
            missing = True
            if action == "plan":
                print(
                    "[PLAN] Install this CA into Linux trust stores "
                    "using mkcert -install"
                )

        if domains.is_wsl():
            print("[WSL] Windows trust check")

            windows_trusted = check_windows(thumbprint)

            if windows_trusted is None:
                return 2

            if not windows_trusted:
                missing = True

                if action == "plan":
                    print(
                        "[PLAN] Import public rootCA.pem into "
                        "Windows CurrentUser Root"
                    )

    elif system == "Windows":
        windows_trusted = check_windows(thumbprint)

        if windows_trusted is None:
            return 2

        missing = not windows_trusted

        if missing and action == "plan":
            print("[PLAN] Install CA into Windows CurrentUser Root")

    else:
        print("[ERROR] Platform trust adapter not implemented")
        return 2

    if action == "plan":
        print("[INFO] Preview only. No system changes made.")
        return 0

    if missing:
        print("[TRUST] Setup required")
        return 1

    print("[OK] Required CA trust checks passed")
    return 0


def setup_linux() -> int:
    """Install local mkcert CA into Linux trust stores."""

    if platform.system() != "Linux":
        print("[ERROR] Linux trust setup requires Linux")
        return 2

    ca_root = certificates.get_ca_root()

    if ca_root is None:
        return 2

    ca_file = ca_root / "rootCA.pem"

    if not ca_file.is_file():
        print("[MISSING] Root CA certificate")
        print("[INFO] Generate the CA first")
        return 1

    if check_linux(ca_file):
        print("[OK] Linux CA trust already configured")
        return 0

    thumbprint = get_thumbprint(ca_file)

    if thumbprint is None:
        print("[ERROR] Cannot identify Root CA")
        return 2

    print("[PLAN] Install local mkcert CA into Linux trust stores")
    print(f"[CA] {ca_file}")
    print(f"[THUMBPRINT] {thumbprint}")

    try:
        answer = input(
            "Trust this CA on Linux? [y/N]: "
        ).strip().lower()
    except EOFError:
        print("[CANCELLED] Interactive confirmation required")
        return 1

    if answer != "y":
        print("[CANCELLED] CA trust installation declined")
        return 1

    try:
        result = subprocess.run(
            ["mkcert", "-install"],
            check=False,
        )
    except OSError as error:
        print(f"[ERROR] Cannot execute mkcert: {error}")
        return 2

    if result.returncode != 0:
        print("[ERROR] mkcert -install failed")
        return 2

    if not check_linux(ca_file):
        print("[ERROR] CA trust verification failed")
        return 2

    print("[OK] Linux CA trust configured successfully")
    return 0



def main() -> int:
    parser = argparse.ArgumentParser(
        description="Manage local CA trust"
    )

    parser.add_argument(
        "action",
        choices=["check", "plan", "setup-linux"],
    )

    args = parser.parse_args()

    if args.action == "setup-linux":
        return setup_linux()

    return inspect(args.action)


if __name__ == "__main__":
    raise SystemExit(main())
