"""Scoped NSS SQL trust operations for a reviewed public development CA.

Callers authorize CA provenance and choose the browser database. This module
never discovers browser profiles, installs system trust, or imports private
keys. It only operates as the ordinary account owner, inside that user's home.
"""
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import shutil
import ssl
import stat
import subprocess
import tempfile

from scripts.dev.configuration import private_primary_group

TRUST = "C,,"
PREFIX = "ft-transcendence-"
DATABASE_FILES = ("cert9.db", "key4.db", "pkcs11.txt")
LEGACY_FILES = ("cert8.db", "key3.db", "secmod.db")
PEM = re.compile(rb"-----BEGIN CERTIFICATE-----\s+[A-Za-z0-9+/=\s]+-----END CERTIFICATE-----\s*")
ROW = re.compile(r"(.+?)\s+([A-Za-z]*,[A-Za-z]*,[A-Za-z]*)\s*")


class NSSError(Exception):
    """Safe diagnostic; never expose subprocess output or certificate contents."""


@dataclass(frozen=True)
class PublicCA:
    der: bytes
    fingerprint: str

    @property
    def nickname(self):
        return PREFIX + self.fingerprint


@dataclass(frozen=True)
class Entry:
    nickname: str
    trust: str
    fingerprint: str

    @property
    def server_trusted(self):
        ssl_flags = self.trust.split(",")[0]
        return "C" in ssl_flags and "p" not in ssl_flags


def execute(tool, *args, data=None, allow_no_keys=False):
    executable = shutil.which(tool)
    if executable is None:
        raise NSSError(f"Required tool unavailable: {tool}")
    try:
        result = subprocess.run([executable, *args], input=data if data is not None else b"",
                                capture_output=True, timeout=20, check=False,
                                env={**os.environ, "LC_ALL": "C"})
    except (OSError, subprocess.SubprocessError):
        raise NSSError(f"{tool} unavailable or timed out") from None
    if result.returncode:
        # certutil -K reports an authenticated but empty key store as exit 255.
        # Do not confuse that specific diagnostic with authentication failure.
        if (allow_no_keys and tool == "certutil" and result.returncode == 255
                and (result.stdout + result.stderr).splitlines() == [
                    b'certutil: Checking token "NSS Certificate DB" in slot "NSS User Private Key and Certificate Services"',
                    b"certutil: no keys found"]):
            return b""
        raise NSSError(f"{tool} operation failed; manual review required")
    return result.stdout


def safe_meta(path, *, directory=False, allow_root_owner=False):
    info = path.lstat()
    correct_type = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    root_owned_public = allow_root_owner and not directory and info.st_uid == 0
    if (not correct_type or (info.st_uid != os.getuid() and not root_owned_public)
            or (root_owned_public and info.st_mode & 0o022) or info.st_mode & 0o002
            or (info.st_mode & 0o020 and not private_primary_group(info.st_uid, info.st_gid))
            or (not directory and info.st_nlink != 1)):
        raise NSSError("Unsafe NSS path or CA file; manual review required")
    return info


def read_ca(path, *, allow_root_owner=False):
    """Read exactly one public PEM certificate; private keys are never accepted."""
    try:
        if safe_meta(path, allow_root_owner=allow_root_owner).st_size > 65536:
            raise NSSError("Expected exactly one public PEM certificate")
        raw = path.read_bytes()
        if len(raw) > 65536 or not PEM.fullmatch(raw.strip()):
            raise NSSError("Expected exactly one public PEM certificate")
        der = ssl.PEM_cert_to_DER_cert(raw.decode("ascii").strip())
        canonical = execute("openssl", "x509", "-inform", "DER", "-outform", "DER", data=der)
        if canonical != der:
            raise NSSError("Invalid or noncanonical CA certificate")
    except (OSError, ValueError, UnicodeError):
        raise NSSError("Cannot read public CA certificate") from None
    return PublicCA(der, hashlib.sha256(der).hexdigest())


def validate_ca(ca):
    """Require a currently valid, self-signed CA before granting trust."""
    constraints = execute("openssl", "x509", "-inform", "DER", "-noout", "-ext",
                          "basicConstraints", data=ca.der)
    if b"CA:TRUE" not in constraints:
        raise NSSError("Certificate is not a CA")
    # Use the same reviewed bytes for validation and import, not a reopened path.
    pem = ssl.DER_cert_to_PEM_cert(ca.der).encode("ascii")
    # A temporary public certificate avoids ambient system trust and source drift.
    with tempfile.TemporaryDirectory(prefix="ft-nss-ca-") as directory:
        path = Path(directory) / "ca.pem"
        path.write_bytes(pem)
        execute("openssl", "verify", "-check_ss_sig", "-no-CApath", "-no-CAstore",
                "-CAfile", str(path), str(path))


def database_state(path, *, create=False):
    """Return True for an existing complete SQL DB, False for absent/empty.

    Never initialize a partial, legacy, or nonempty unknown directory.
    """
    home = Path.home()
    if (os.getuid() == 0 or os.geteuid() != os.getuid() or not path.is_absolute()
            or path == home or ".." in path.parts or not path.is_relative_to(home)):
        raise NSSError("NSS database must be inside the ordinary user's home")
    try:
        safe_meta(home, directory=True)
        current = home
        for part in path.relative_to(home).parts:
            current /= part
            if not current.exists() and not current.is_symlink():
                if not create:
                    return False
                current.mkdir(mode=0o700)
            safe_meta(current, directory=True)
        present = set(p.name for p in path.iterdir())
        if not present:
            return False
        if set(LEGACY_FILES) & present or not set(DATABASE_FILES) <= present:
            raise NSSError("Incomplete or legacy NSS database; refusing initialization")
        for name in present:
            if name in DATABASE_FILES or name.startswith(("cert9.db-", "key4.db-")):
                info = safe_meta(path / name)
                if name in DATABASE_FILES and not info.st_size:
                    raise NSSError("Incomplete NSS database; manual review required")
        return True
    except OSError:
        raise NSSError("Cannot safely inspect NSS database") from None


def certutil(path, *args, data=None, allow_no_keys=False):
    return execute("certutil", *args, "-d", "sql:" + str(path), data=data,
                   allow_no_keys=allow_no_keys)


def require_empty_password(path):
    """Authenticate read-only before mutation; never reset a protected token."""
    with tempfile.TemporaryDirectory(prefix="ft-nss-auth-") as directory:
        password = Path(directory) / "empty"
        password.write_bytes(b"\n")
        try:
            certutil(path, "-K", "-h", "internal", "-f", str(password), allow_no_keys=True)
        except NSSError:
            raise NSSError("NSS authentication not verified; manual review required") from None


def parse_listing(raw):
    """Parse the C-locale certutil listing; reject ambiguous/unexpected rows."""
    try:
        lines = [line for line in raw.decode("utf-8").splitlines() if line.strip()]
    except UnicodeError:
        raise NSSError("Invalid NSS certificate listing") from None
    if (len(lines) < 2 or lines[0].split() != ["Certificate", "Nickname", "Trust", "Attributes"]
            or lines[1].strip() != "SSL,S/MIME,JAR/XPI"):
        raise NSSError("Unexpected NSS certificate listing")
    entries = {}
    for line in lines[2:]:
        match = ROW.fullmatch(line)
        if not match or match[1] in entries:
            raise NSSError("Ambiguous NSS certificate listing")
        entries[match[1]] = match[2]
    return entries


def entries(path):
    if shutil.which("certutil") is None:
        raise NSSError("Required tool unavailable: certutil")
    if not database_state(path):
        return []
    listing = parse_listing(certutil(path, "-L", "-h", "internal"))
    result = []
    for nickname, trust in listing.items():
        der = certutil(path, "-L", "-n", nickname, "-r", "-h", "internal")
        if not der:
            raise NSSError("Cannot identify NSS certificate")
        result.append(Entry(nickname, trust, hashlib.sha256(der).hexdigest()))
    return result


def matching(path, ca):
    items = entries(path)
    managed = [item for item in items if item.nickname == ca.nickname]
    if managed and managed[0].fingerprint != ca.fingerprint:
        raise NSSError("Managed NSS nickname collision; refusing modification")
    matches = [item for item in items if item.fingerprint == ca.fingerprint]
    if len(matches) > 1:
        raise NSSError("Ambiguous matching NSS certificates; manual review required")
    return matches[0] if matches else None


def check(path, ca_file):
    """Read-only result: trusted, missing, or untrusted (not browser readiness)."""
    ca = read_ca(ca_file)
    validate_ca(ca)
    item = matching(path, ca)
    if item is None:
        return "missing"
    if item.nickname == ca.nickname and item.trust != TRUST:
        return "untrusted"
    return "trusted" if item.server_trusted else "untrusted"


def install(path, ca_file):
    """Import only a missing CA; never change existing trust or nicknames."""
    ca = read_ca(ca_file)
    validate_ca(ca)
    item = matching(path, ca)
    if item is not None:
        if not item.server_trusted or (item.nickname == ca.nickname and item.trust != TRUST):
            raise NSSError("Existing CA has unexpected trust; manual review required")
        return "unchanged"
    if not database_state(path, create=True):
        certutil(path, "-N", "--empty-password")
        if not database_state(path):
            raise NSSError("NSS initialization not verified")
    require_empty_password(path)
    # Recheck immediately before import, including nickname and existing trust.
    if matching(path, ca) is not None:
        raise NSSError("NSS database changed during preparation; retry after review")
    certutil(path, "-A", "-n", ca.nickname, "-t", TRUST, data=ca.der)
    item = matching(path, ca)
    if item is None or item.nickname != ca.nickname or item.trust != TRUST:
        raise NSSError("NSS import not verified; manual review required")
    return "installed"


def uninstall(path, ca_file):
    """Remove only the project-named CA with its exact fingerprint and flags.

    Externally installed aliases are preserved. Expired CA certificates can
    still be removed; only installation requires a currently valid chain.
    """
    ca = read_ca(ca_file)
    item = matching(path, ca)
    if item is None or item.nickname != ca.nickname:
        return "unchanged"
    if item.trust != TRUST:
        raise NSSError("Managed CA trust changed; refusing removal")
    require_empty_password(path)
    certutil(path, "-D", "-n", ca.nickname)
    if matching(path, ca) is not None:
        raise NSSError("NSS removal not verified; manual review required")
    return "removed"
