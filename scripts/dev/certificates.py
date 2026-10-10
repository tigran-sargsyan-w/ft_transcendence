
"""Local TLS certificate management for ft_transcendence."""

import argparse
import platform
import shutil
import subprocess
import re
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


def validate_certificate(
    ca_root: Path,
    domain: str = "transcendence.test",
) -> tuple[bool, str]:
    """Validate the TLS certificate, chain and private key."""

    if not shutil.which("openssl"):
        return False, "OpenSSL is not installed"

    def run(*args: str):
        return subprocess.run(
            ["openssl", *args],
            capture_output=True,
            text=True,
            check=False,
        )

    # Reject certificates expiring within 30 days.
    expiry = run(
        "x509", "-in", str(CERT_FILE),
        "-noout", "-checkend", str(30 * 24 * 60 * 60),
    )
    if expiry.returncode != 0:
        return False, "Certificate invalid or expiring within 30 days"

    # Validate the certificate chain against our local CA.
    chain = run(
        "verify",
        "-CAfile", str(ca_root / "rootCA.pem"),
        "-purpose", "sslserver",
        "-verify_hostname", domain,
        str(CERT_FILE),
    )
    if chain.returncode != 0:
        return False, "Certificate chain or hostname verification failed"

    # Verify the explicitly required SAN entries.
    san = run(
        "x509", "-in", str(CERT_FILE),
        "-noout", "-ext", "subjectAltName",
    )
    if san.returncode != 0:
        return False, "Cannot read Subject Alternative Names"

    dns_names = {
        value.lower()
        for value in re.findall(r"DNS:([^,\s]+)", san.stdout)
    }
    ip_addresses = set(
        re.findall(r"IP Address:([^,\s]+)", san.stdout)
    )

    required_dns = {domain.lower(), "localhost"}
    required_ips = {"127.0.0.1"}

    if not required_dns.issubset(dns_names):
        return False, "Missing required DNS SAN entries"

    if not required_ips.issubset(ip_addresses):
        return False, "Missing required IP SAN entries"

    # Compare public keys extracted from certificate and private key.
    certificate_key = run(
        "x509", "-in", str(CERT_FILE),
        "-pubkey", "-noout",
    )
    private_key = run(
        "pkey", "-in", str(KEY_FILE),
        "-pubout", "-passin", "pass:",
    )

    if (
        certificate_key.returncode != 0
        or private_key.returncode != 0
    ):
        return False, "Cannot extract certificate or private public key"

    if certificate_key.stdout.strip() != private_key.stdout.strip():
        return False, "TLS certificate and private key do not match"

    return True, "Certificate cryptographically valid"


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

    valid, message = validate_certificate(ca_root)

    if not valid:
        print(f"[INVALID] {message}")
        return 1

    print(f"[OK] {message}")
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
