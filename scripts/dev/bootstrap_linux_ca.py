"""Guarded browser TLS setup on native Linux. No automatic Windows trust."""
import hashlib
import os
from pathlib import Path
import stat
import subprocess

from scripts.dev import ca_trust, certificates, domains, linux_hosts, nginx_tls_access

MARKER = ".ft-transcendence-initialize-ca.sha256"


class LinuxCAError(Exception):
    """Public diagnostics never include private key or subprocess output."""


def native_linux():
    if os.geteuid() == 0 or domains.platform.system() != "Linux" or domains.is_wsl():
        raise LinuxCAError("Automatic host preparation requires native Linux and an ordinary user")
    if os.environ.get("DEV_DOMAIN", domains.DEFAULT_DOMAIN) != domains.DEFAULT_DOMAIN:
        raise LinuxCAError("Automatic host preparation requires the default development domain")


def safe_directory(path):
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
        raise LinuxCAError("Unsafe local CA directory; manual review required")


def safe_file(path, *, private=False):
    info = path.lstat()
    forbidden = 0o077 if private else 0o022
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
            or info.st_nlink != 1 or info.st_mode & forbidden or info.st_size == 0):
        raise LinuxCAError("Unsafe local CA file; manual review required")


def ca_root():
    home = Path.home()
    expected = Path(os.environ.get("XDG_DATA_HOME") or home / ".local/share") / "mkcert"
    if not expected.is_absolute() or ".." in expected.parts or not expected.is_relative_to(home):
        raise LinuxCAError("Unexpected CA location; manual review required")
    override = os.environ.get("CAROOT")
    if override and Path(override) != expected:
        raise LinuxCAError("Unexpected CAROOT override; manual review required")
    current = home
    safe_directory(current)
    for part in expected.relative_to(home).parts:
        current /= part
        if current.exists() or current.is_symlink():
            safe_directory(current)
    if certificates.get_ca_root() != expected:
        raise LinuxCAError("mkcert CA location differs from expected user directory")
    return expected


def fingerprint(root):
    safe_directory(root)
    cert = root / "rootCA.pem"
    key = root / "rootCA-key.pem"
    safe_file(cert)
    safe_file(key, private=True)

    def openssl(*args):
        result = subprocess.run(["openssl", *args], capture_output=True, check=False, timeout=20)
        if result.returncode:
            raise LinuxCAError("Local CA cryptographic validation failed")
        return result.stdout

    openssl("verify", "-check_ss_sig", "-CAfile", str(cert), str(cert))
    constraints = openssl("x509", "-in", str(cert), "-noout", "-ext", "basicConstraints")
    if b"CA:TRUE" not in constraints:
        raise LinuxCAError("Local certificate is not a CA")
    cert_key = openssl("x509", "-in", str(cert), "-noout", "-pubkey")
    private_key = openssl("pkey", "-in", str(key), "-pubout", "-passin", "pass:")
    if not cert_key.strip() or cert_key.strip() != private_key.strip():
        raise LinuxCAError("Local CA certificate and key do not match")
    return hashlib.sha256(cert.read_bytes()).hexdigest()


def record_fingerprint(root, value):
    # Provenance for accidental drift, not a boundary against the account owner.
    fd = os.open(root / MARKER, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="ascii") as stream:
        stream.write(value + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def present(path):
    return path.exists() or path.is_symlink()


def inspect_ca(root):
    cert = root / "rootCA.pem"
    key = root / "rootCA-key.pem"
    marker = root / MARKER
    if not present(cert) and not present(key):
        if present(marker):
            raise LinuxCAError("CA provenance record exists but CA is absent")
        return None
    if not present(cert) or not present(key):
        raise LinuxCAError("Incomplete local CA; refusing replacement")
    value = fingerprint(root)
    if present(marker):
        safe_file(marker, private=True)
        if marker.read_text(encoding="ascii") != value + "\n":
            raise LinuxCAError("Local CA differs from its initialization record")
    elif not ca_trust.check_linux(cert):
        raise LinuxCAError("Unknown untrusted local CA; use explicit host preparation after review")
    return value


def prepare():
    native_linux()
    root = ca_root()
    previous = inspect_ca(root)
    linux_hosts.append_text(linux_hosts.read_hosts(), domains.DEFAULT_DOMAIN)
    if certificates.setup() != 0:
        raise LinuxCAError("Browser TLS setup failed; existing files retained")
    nginx_tls_access.check_files()
    if ca_root() != root:
        raise LinuxCAError("CA location changed during preparation")
    value = fingerprint(root)
    if previous is None:
        record_fingerprint(root, value)
    elif value != previous:
        raise LinuxCAError("CA identity changed during preparation")
    cert = root / "rootCA.pem"
    if not ca_trust.check_linux(cert):
        if inspect_ca(root) != value or ca_trust.install_linux(cert) != 0:
            raise LinuxCAError("Linux CA trust installation failed")
        if fingerprint(root) != value:
            raise LinuxCAError("CA identity changed during trust installation")
    if linux_hosts.setup(domains.DEFAULT_DOMAIN, initializing=True) != 0:
        raise LinuxCAError("Linux hosts preparation failed")
    return 0


def main():
    try:
        return prepare()
    except (LinuxCAError, OSError, ValueError, subprocess.SubprocessError,
            nginx_tls_access.TLSAccessError) as exc:
        print("ERROR:", str(exc) if isinstance(exc, LinuxCAError)
              else "Linux host preparation failed; inspect with --prepare-host")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
