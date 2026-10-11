
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


def setup_windows(ca_file: Path, thumbprint: str) -> int:
    """Install the public CA into Windows CurrentUser Root."""

    script_path = Path(__file__).with_name("windows_ca_trust.ps1")

    if not script_path.is_file():
        print("[ERROR] Windows CA trust adapter not found")
        return 2

    system = platform.system()

    if system == "Linux":
        # Convert WSL paths to Windows paths.
        ca_result = run("wslpath", "-w", str(ca_file))
        script_result = run("wslpath", "-w", str(script_path))

        if (
            ca_result.returncode != 0
            or script_result.returncode != 0
        ):
            print("[ERROR] Cannot convert WSL paths")
            return 2

        windows_ca = ca_result.stdout.strip()
        windows_script = script_result.stdout.strip()
    else:
        windows_ca = str(ca_file)
        windows_script = str(script_path)

    print("[PLAN] Install public CA into Windows CurrentUser Root")
    print(f"[THUMBPRINT] {thumbprint}")
    print("[INFO] Windows will trust certificates issued by this CA")

    try:
        answer = input(
            "Trust this CA on Windows? [y/N]: "
        ).strip().lower()
    except EOFError:
        print("[CANCELLED] Interactive confirmation required")
        return 1

    if answer != "y":
        print("[CANCELLED] Windows CA trust installation declined")
        return 1

    try:
        result = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy", "Bypass",
                "-File", windows_script,
                "-Action", "setup",
                "-CaPath", windows_ca,
                "-ExpectedThumbprint", thumbprint,
                "-Apply",
            ],
            check=False,
        )
    except OSError as error:
        print(f"[ERROR] Windows CA setup failed: {error}")
        return 2

    if result.returncode != 0:
        print("[ERROR] Windows CA trust installation failed")
        return 2

    return 0


def setup() -> int:
    """Configure CA trust for the current environment."""

    system = platform.system()

    if system not in ("Linux", "Windows"):
        print("[ERROR] Unsupported platform")
        return 2

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

    print("[TRUST] Unified CA trust setup")
    print(f"[SYSTEM] {system}")
    print(f"[THUMBPRINT] {thumbprint}")

    # Linux trust.
    if system == "Linux":
        if not check_linux(ca_file):
            result = setup_linux()

            if result != 0:
                return result

    # Windows trust (native Windows or WSL).
    needs_windows = (
        system == "Windows"
        or (system == "Linux" and domains.is_wsl())
    )

    if needs_windows:
        trusted = check_windows(thumbprint)

        if trusted is None:
            return 2

        if not trusted:
            result = setup_windows(ca_file, thumbprint)

            if result != 0:
                return result

    # Final verification of all required trust stores.
    return inspect("check")


def uninstall_plan() -> int:
    """Preview removal of local CA trust without modifying stores."""

    system = platform.system()

    if system != "Linux":
        print("[ERROR] Uninstall preview currently supports Linux/WSL")
        return 2

    ca_root = certificates.get_ca_root()

    if ca_root is None:
        return 2

    ca_file = ca_root / "rootCA.pem"

    if not ca_file.is_file():
        print("[ERROR] Root CA certificate not found")
        return 2

    thumbprint = get_thumbprint(ca_file)

    if thumbprint is None:
        print("[ERROR] Cannot identify Root CA")
        return 2

    print("[TRUST] CA trust removal preview")
    print(f"[CA] {ca_file}")
    print(f"[THUMBPRINT] {thumbprint}")

    # Linux trust.
    if check_linux(ca_file):
        print("[PLAN] Linux: remove mkcert CA trust")
    else:
        print("[OK] Linux: CA already untrusted")

    # Windows trust when running under WSL.
    if domains.is_wsl():
        print("[WSL] Windows trust check")

        trusted = check_windows(thumbprint)

        if trusted is None:
            return 2

        print(
            "[PLAN] Windows: remove matching CA from "
            "CurrentUser Root if present"
        )
        print("[PRESERVED] Windows LocalMachine Root")

    print("[PRESERVED] rootCA.pem")
    print("[PRESERVED] rootCA-key.pem")
    print("[PRESERVED] Project TLS certificates")
    print("[INFO] Preview only. No changes made")

    return 0


def uninstall_linux() -> int:
    """Remove mkcert CA trust from Linux without deleting CA files."""

    if platform.system() != "Linux":
        print("[ERROR] Linux trust uninstall requires Linux")
        return 2

    ca_root = certificates.get_ca_root()

    if ca_root is None:
        return 2

    ca_file = ca_root / "rootCA.pem"
    ca_key = ca_root / "rootCA-key.pem"

    if not ca_file.is_file():
        print("[ERROR] Root CA certificate not found")
        return 2

    thumbprint = get_thumbprint(ca_file)

    if thumbprint is None:
        print("[ERROR] Cannot identify Root CA")
        return 2

    if not check_linux(ca_file):
        print("[OK] Linux OpenSSL CA trust already absent")
        return 0

    print("[PLAN] Remove local mkcert CA trust from Linux")
    print(f"[CA] {ca_file}")
    print(f"[THUMBPRINT] {thumbprint}")

    print("[PRESERVED] Root CA certificate and private key")
    print("[PRESERVED] Project TLS certificates")
    print("[PRESERVED] Windows CA trust")

    print(
        "[WARNING] mkcert -uninstall may also remove "
        "CA trust from Linux browser/NSS stores"
    )

    try:
        answer = input(
            "Remove this CA trust from Linux? [y/N]: "
        ).strip().lower()
    except EOFError:
        print("[CANCELLED] Interactive confirmation required")
        return 1

    if answer != "y":
        print("[CANCELLED] Linux CA trust uninstall declined")
        return 1

    try:
        result = subprocess.run(
            ["mkcert", "-uninstall"],
            check=False,
        )
    except OSError as error:
        print(f"[ERROR] Cannot execute mkcert: {error}")
        return 2

    if result.returncode != 0:
        print("[ERROR] mkcert -uninstall failed")
        return 2

    if check_linux(ca_file):
        print("[ERROR] CA remains trusted in Linux OpenSSL")
        return 2

    if not ca_file.is_file() or not ca_key.is_file():
        print("[ERROR] CA files unexpectedly missing")
        return 2

    print("[OK] Linux OpenSSL CA trust removed")
    print("[OK] Root CA files preserved")

    return 0


def uninstall_windows(ca_file: Path, thumbprint: str) -> int:
    """Remove matching CA from Windows CurrentUser Root."""

    script_path = Path(__file__).with_name(
        "windows_ca_trust.ps1"
    )

    if not script_path.is_file():
        print("[ERROR] Windows trust adapter not found")
        return 2

    system = platform.system()

    if system == "Linux":
        if not domains.is_wsl():
            print("[ERROR] Windows adapter requires WSL")
            return 2

        try:
            ca_result = run("wslpath", "-w", str(ca_file))
            script_result = run("wslpath", "-w", str(script_path))
        except OSError as error:
            print(f"[ERROR] WSL path conversion: {error}")
            return 2

        if (
            ca_result.returncode != 0
            or script_result.returncode != 0
        ):
            print("[ERROR] Cannot convert WSL paths")
            return 2

        windows_ca = ca_result.stdout.strip()
        windows_script = script_result.stdout.strip()

    elif system == "Windows":
        windows_ca = str(ca_file)
        windows_script = str(script_path)

    else:
        print("[ERROR] Unsupported Windows adapter platform")
        return 2

    command = [
        "powershell.exe",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy", "Bypass",
        "-File", windows_script,
        "-Action", "uninstall",
        "-CaPath", windows_ca,
        "-ExpectedThumbprint", thumbprint,
    ]

    # Preview and inspect CurrentUser Root specifically.
    try:
        preview = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as error:
        print(f"[ERROR] Windows trust check: {error}")
        return 2

    print(preview.stdout, end="")

    if (
        preview.returncode == 0
        and "[OK] CA is not in CurrentUser Root"
        in preview.stdout
    ):
        return 0

    if (
        preview.returncode != 1
        or "[PLAN] Remove matching CA from CurrentUser Root"
        not in preview.stdout
    ):
        print("[ERROR] Windows uninstall preview failed")
        print(preview.stderr.strip())
        return 2

    try:
        answer = input(
            "Remove this CA trust from Windows? [y/N]: "
        ).strip().lower()
    except EOFError:
        print("[CANCELLED] Interactive confirmation required")
        return 1

    if answer != "y":
        print("[CANCELLED] Windows CA trust uninstall declined")
        return 1

    # Apply the change only after explicit confirmation.
    try:
        result = subprocess.run(
            [*command, "-Apply"],
            check=False,
        )
    except OSError as error:
        print(f"[ERROR] Windows uninstall: {error}")
        return 2

    if result.returncode != 0:
        print("[ERROR] Windows CA trust uninstall failed")
        return 2

    # Verify the certificate is absent from CurrentUser Root.
    try:
        verification = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as error:
        print(f"[ERROR] Windows verification: {error}")
        return 2

    if (
        verification.returncode != 0
        or "[OK] CA is not in CurrentUser Root"
        not in verification.stdout
    ):
        print("[ERROR] Windows CA trust removal not verified")
        return 2

    print("[OK] Windows CurrentUser CA trust removed")
    return 0


def uninstall() -> int:
    """Remove CA trust for the current environment."""

    system = platform.system()

    if system not in ("Linux", "Windows"):
        print("[ERROR] Unsupported platform")
        return 2

    ca_root = certificates.get_ca_root()

    if ca_root is None:
        return 2

    ca_file = ca_root / "rootCA.pem"

    if not ca_file.is_file():
        print("[ERROR] Root CA certificate not found")
        return 2

    thumbprint = get_thumbprint(ca_file)

    if thumbprint is None:
        print("[ERROR] Cannot identify Root CA")
        return 2

    print("[TRUST] Unified CA trust uninstall")
    print(f"[SYSTEM] {system}")
    print(f"[THUMBPRINT] {thumbprint}")

    print("[PRESERVED] CA certificate and private key")
    print("[PRESERVED] Project TLS certificates")

    if system == "Linux":
        result = uninstall_linux()

        if result != 0:
            return result

    needs_windows = (
        system == "Windows"
        or (system == "Linux" and domains.is_wsl())
    )

    if needs_windows:
        result = uninstall_windows(ca_file, thumbprint)

        if result != 0:
            print(
                "[INFO] Some trust stores may already "
                "have been modified"
            )
            print(
                "[INFO] Run 'python3 -m "
                "scripts.dev.ca_trust setup' to restore trust"
            )
            return result

    print("[OK] Required CA trust removal completed")
    print("[OK] CA files and project TLS certificates preserved")

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Manage local CA trust"
    )

    parser.add_argument(
        "action",
        choices=[
            "check",
            "plan",
            "setup",
            "setup-linux",
            "uninstall-plan",
            "uninstall-linux",
            "uninstall",
        ],
    )

    args = parser.parse_args()

    if args.action == "setup":
        return setup()

    if args.action == "setup-linux":
        return setup_linux()

    if args.action == "uninstall-plan":
        return uninstall_plan()

    if args.action == "uninstall-linux":
        return uninstall_linux()

    if args.action == "uninstall":
        return uninstall()

    return inspect(args.action)


if __name__ == "__main__":
    raise SystemExit(main())
