
"""Local TLS certificate management for ft_transcendence."""

import argparse
import platform
import shutil
import subprocess
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CERT_DIR = PROJECT_ROOT / "infra" / "nginx" / "certs"

CERT_FILE = CERT_DIR / "cert.pem"
KEY_FILE = CERT_DIR / "key.pem"


def get_ca_root() -> Path | None:
    mkcert = shutil.which("mkcert")

    if not mkcert:
        print("[MISSING] mkcert is not installed")
        return None

    result = subprocess.run(
        [mkcert, "-CAROOT"],
        capture_output=True,
        text=True,
        check=False,
    )

    if result.returncode != 0:
        print("[ERROR] Unable to locate mkcert CA directory")
        return None

    path = result.stdout.strip()

    if not path:
        print("[ERROR] mkcert returned an empty CA directory")
        return None

    return Path(path)


def check() -> int:
    print("[TLS] Certificate diagnostics")
    print(f"[SYSTEM] {platform.system()}")

    ca_root = get_ca_root()

    if ca_root is None:
        return 2

    print(f"[CA ROOT] {ca_root}")

    files = {
        "Root CA": ca_root / "rootCA.pem",
        "Root CA private key": ca_root / "rootCA-key.pem",
        "TLS certificate": CERT_FILE,
        "TLS private key": KEY_FILE,
    }

    missing = False

    for label, path in files.items():
        if path.is_file():
            print(f"[OK] {label}")
        else:
            print(f"[MISSING] {label}")
            missing = True

    if missing:
        print("[TLS] Setup required")
        return 1

    print("[TLS] Files found; cryptographic validation pending")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Manage local TLS certificates"
    )

    parser.add_argument(
        "action",
        choices=["check"],
    )

    args = parser.parse_args()

    if args.action == "check":
        return check()

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
