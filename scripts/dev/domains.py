
import os
import platform
import subprocess
import sys
from pathlib import Path


DOMAIN = os.environ.get("DEV_DOMAIN", "transcendence.test").strip().lower()
EXPECTED_IP = "127.0.0.1"


def is_wsl() -> bool:
    return (
        platform.system() == "Linux"
        and "microsoft" in platform.release().lower()
    )


def read_hosts() -> dict[str, str]:
    system = platform.system()

    if system == "Windows":
        root = Path(os.environ["SystemRoot"])
        path = root / "System32" / "drivers" / "etc" / "hosts"
        return {
            "Windows": path.read_text(encoding="utf-8", errors="replace")
        }

    if system not in ("Linux", "Darwin"):
        raise RuntimeError(f"Unsupported operating system: {system}")

    hosts = {
        "WSL" if is_wsl() else system:
            Path("/etc/hosts").read_text(encoding="utf-8")
    }

    if is_wsl():
        command = (
            "$p = Join-Path $env:SystemRoot "
            "'System32\\drivers\\etc\\hosts'; "
            "Get-Content -LiteralPath $p -Raw"
        )

        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive",
             "-Command", command],
            capture_output=True,
            text=True,
            check=True,
        )
        hosts["Windows"] = result.stdout

    return hosts


def find_mappings(content: str, domain: str) -> list[str]:
    addresses = []

    for line in content.splitlines():
        fields = line.split("#", 1)[0].split()

        if len(fields) < 2:
            continue

        if domain in (name.lower() for name in fields[1:]):
            addresses.append(fields[0])

    return addresses


def check() -> int:
    if DOMAIN == "localhost":
        print("[OK] localhost requires no custom hosts mapping")
        return 0

    if not DOMAIN.endswith(".test") or any(
        char not in "abcdefghijklmnopqrstuvwxyz0123456789.-"
        for char in DOMAIN
    ):
        print("[ERROR] DEV_DOMAIN must be localhost or a .test domain")
        return 2

    print(f"[DOMAIN] Checking {DOMAIN}")
    print(f"[SYSTEM] {platform.system()}")
    print(f"[WSL] {'yes' if is_wsl() else 'no'}")

    missing = False
    conflict = False

    for system, content in read_hosts().items():
        addresses = find_mappings(content, DOMAIN)

        if not addresses:
            print(f"[MISSING] {system}: no mapping")
            missing = True
        elif addresses == [EXPECTED_IP]:
            print(f"[OK] {system}: {DOMAIN} -> {EXPECTED_IP}")
        else:
            print(f"[CONFLICT] {system}: {addresses}")
            conflict = True

    if conflict:
        return 2
    return 1 if missing else 0


if __name__ == "__main__":
    try:
        sys.exit(check())
    except (OSError, subprocess.CalledProcessError) as error:
        print(f"[ERROR] {error}", file=sys.stderr)
        sys.exit(2)
