
"""Local TLS certificate management for ft_transcendence."""

import argparse
import os
import platform
import re
import shutil
import subprocess
import tempfile

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CERT_DIR = PROJECT_ROOT / "infra" / "nginx" / "certs"

CERT_FILE = CERT_DIR / "cert.pem"
KEY_FILE = CERT_DIR / "key.pem"

DEFAULT_DOMAIN = "transcendence.test"


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
    domain: str = DEFAULT_DOMAIN,
    *,
    cert_file: Path | None = None,
    key_file: Path | None = None,
) -> tuple[bool, str]:
    """Validate certificate, chain, SAN and private key."""

    cert_file = cert_file if cert_file is not None else CERT_FILE
    key_file = key_file if key_file is not None else KEY_FILE

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
        "x509",
        "-in", str(cert_file),
        "-noout",
        "-checkend", str(30 * 24 * 60 * 60),
    )

    if expiry.returncode != 0:
        return False, "Certificate invalid or expiring within 30 days"

    # Verify certificate chain and hostname.
    chain = run(
        "verify",
        "-CAfile", str(ca_root / "rootCA.pem"),
        "-purpose", "sslserver",
        "-verify_hostname", domain,
        str(cert_file),
    )

    if chain.returncode != 0:
        return False, "Certificate chain or hostname verification failed"

    # Verify required SAN entries.
    san = run(
        "x509",
        "-in", str(cert_file),
        "-noout",
        "-ext", "subjectAltName",
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

    # Compare public keys from certificate and private key.
    certificate_key = run(
        "x509",
        "-in", str(cert_file),
        "-pubkey",
        "-noout",
    )

    private_key = run(
        "pkey",
        "-in", str(key_file),
        "-pubout",
        "-passin", "pass:",
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
    """Check certificate files and cryptographic validity."""

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


def setup() -> int:
    """Generate missing certificates without overwriting existing ones."""

    print("[TLS] Certificate setup")

    ca_root = get_ca_root()

    if ca_root is None:
        return 2

    # Reuse a valid certificate pair.
    if CERT_FILE.exists() or KEY_FILE.exists():
        if not CERT_FILE.is_file() or not KEY_FILE.is_file():
            print("[ERROR] Incomplete TLS certificate pair")
            print("[INFO] Refusing to overwrite existing files")
            return 2

        valid, message = validate_certificate(ca_root)

        if not valid:
            print(f"[INVALID] {message}")
            print("[INFO] Existing files were not modified")
            return 2

        print("[OK] Existing TLS certificate is valid")
        print("[OK] Reusing certificate and private key")
        return 0

    mkcert = shutil.which("mkcert")

    if not mkcert:
        print("[ERROR] mkcert is not installed")
        return 2

    try:
        CERT_DIR.mkdir(
            parents=True,
            exist_ok=True,
            mode=0o700,
        )

        # Stage both files before publishing them.
        with tempfile.TemporaryDirectory(
            prefix=".tls-stage-",
            dir=CERT_DIR,
        ) as staging:
            staging_dir = Path(staging)

            staged_cert = staging_dir / "cert.pem"
            staged_key = staging_dir / "key.pem"

            print("[TLS] Generating certificate using mkcert")

            result = subprocess.run(
                [
                    mkcert,
                    "-cert-file", str(staged_cert),
                    "-key-file", str(staged_key),
                    DEFAULT_DOMAIN,
                    "localhost",
                    "127.0.0.1",
                ],
                capture_output=True,
                text=True,
                check=False,
            )

            if result.returncode != 0:
                print("[ERROR] mkcert generation failed")
                print(result.stderr.strip())
                return 2

            # Restrict access to the private key.
            os.chmod(staged_key, 0o600)

            # Validate before installing generated files.
            valid, message = validate_certificate(
                ca_root,
                cert_file=staged_cert,
                key_file=staged_key,
            )

            if not valid:
                print(f"[ERROR] Generated certificate: {message}")
                return 2

            print("[OK] Generated certificate validated")

            # Never overwrite an existing certificate pair.
            if CERT_FILE.exists() or KEY_FILE.exists():
                print("[ERROR] Certificate files appeared during setup")
                return 2

            # Publish only the verified pair.
            os.replace(staged_key, KEY_FILE)

            try:
                os.replace(staged_cert, CERT_FILE)
            except OSError:
                # Undo our newly installed key if certificate publishing fails.
                KEY_FILE.unlink(missing_ok=True)
                raise

    except (OSError, subprocess.SubprocessError) as error:
        print(f"[ERROR] TLS setup failed: {error}")
        return 2

    print("[OK] TLS certificate generated")
    print(f"[CERT] {CERT_FILE}")
    print(f"[KEY] {KEY_FILE}")
    print(f"[CA ROOT] {ca_root}")
    print("[TRUST] CA trust setup has not been verified")
    print("[INFO] System trust installation is a separate step")

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Manage local TLS certificates"
    )

    parser.add_argument(
        "action",
        choices=["check", "setup"],
    )

    args = parser.parse_args()

    if args.action == "check":
        return check()

    if args.action == "setup":
        return setup()

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
