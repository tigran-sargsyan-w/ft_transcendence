"""Explicit user-level trust for the local Vault development CA.

No Docker operations, system trust changes, key permission changes or TLS
regeneration. The operator authorizes setup; validation is not proof of the
historical origin of an already existing CA. Automatic bootstrap integration
may only import newly generated or previously authorized CA material.
"""
import argparse
from contextlib import contextmanager
import os
from pathlib import Path
import ssl
import stat
import subprocess
import tempfile

from scripts.dev import chromium_trust, nss_trust as nss

ROOT = Path(__file__).resolve().parents[2]
MARKER = ".ft-transcendence-vault-ca.sha256"


def owned_path(path, *, directory=False, private=False, allow_root_owner=False):
    home = Path.home()
    if not path.is_absolute() or not path.is_relative_to(home) or '..' in path.parts:
        raise nss.NSSError("Vault TLS path must be inside the user's home")
    current = home
    nss.safe_meta(current, directory=True)
    for part in path.relative_to(home).parts[:-1]:
        current /= part
        nss.safe_meta(current, directory=True)
    info = nss.safe_meta(path, directory=directory, allow_root_owner=allow_root_owner)
    if private and info.st_mode & 0o077:
        raise nss.NSSError("Unsafe Vault CA private key permissions")


def material_paths():
    home = Path.home()
    state = Path(os.environ.get('XDG_STATE_HOME') or home / '.local/state')
    data = Path(os.environ.get('XDG_DATA_HOME') or home / '.local/share')
    tls = state / 'transcendence/dev/tls'
    override = os.environ.get('FT_DEV_TLS_DIR')
    if override and Path(override) != tls:
        raise nss.NSSError("Unexpected Vault TLS directory override")
    return tls / 'ca.crt', tls / 'vault.crt', data / 'ft-transcendence/vault-ca/ca.key'


@contextmanager
def reviewed_ca():
    """Validate the local CA and leaf, then yield only the reviewed public bytes.

    The Vault server private key is intentionally not read: it belongs to the
    Vault container UID after provisioning. The separate CA key remains local.
    """
    chromium_trust.selected_database()  # Native Linux, ordinary user, safe store.
    ca_file, leaf_file, ca_key = material_paths()
    try:
        owned_path(ca_file)
        # TLS maintenance installs the public leaf from a root-run helper.
        # Only the leaf may be root-owned; CA and NSS ownership stay strict.
        owned_path(leaf_file, allow_root_owner=True)
        owned_path(ca_key, private=True)
        ca = nss.read_ca(ca_file)
        leaf = nss.read_ca(leaf_file, allow_root_owner=True)
        nss.validate_ca(ca)
        subject = nss.execute('openssl', 'x509', '-inform', 'DER', '-noout',
                              '-subject', '-nameopt', 'RFC2253', data=ca.der)
        if subject.strip() != b'subject=CN=Transcendence Vault Development CA':
            raise nss.NSSError("Unexpected Vault CA subject")
        cert_pub = nss.execute('openssl', 'x509', '-inform', 'DER', '-noout',
                               '-pubkey', data=ca.der)
        key_pub = nss.execute('openssl', 'pkey', '-in', str(ca_key), '-passin', 'pass:', '-pubout')
        if not cert_pub.strip() or cert_pub.strip() != key_pub.strip():
            raise nss.NSSError("Vault CA certificate and private key do not match")
        san = nss.execute('openssl', 'x509', '-inform', 'DER', '-noout',
                          '-ext', 'subjectAltName', data=leaf.der)
        names = {name.strip() for name in san.splitlines()[-1].split(b',')} if san else set()
        if not {b'DNS:vault', b'DNS:localhost', b'IP Address:127.0.0.1'} <= names:
            raise nss.NSSError("Vault TLS certificate lacks required SANs")
        with tempfile.TemporaryDirectory(prefix='ft-vault-ca-') as directory:
            public = Path(directory) / 'ca.pem'
            server = Path(directory) / 'vault.pem'
            public.write_text(ssl.DER_cert_to_PEM_cert(ca.der), encoding='ascii')
            server.write_text(ssl.DER_cert_to_PEM_cert(leaf.der), encoding='ascii')
            for option, name in (('-verify_hostname', 'vault'),
                                 ('-verify_hostname', 'localhost'),
                                 ('-verify_ip', '127.0.0.1')):
                nss.execute('openssl', 'verify', '-no-CApath', '-no-CAstore',
                            '-CAfile', str(public), '-purpose', 'sslserver',
                            option, name, str(server))
            yield public, ca.fingerprint
    except OSError:
        raise nss.NSSError("Vault CA or TLS material missing or unsafe") from None


def authorization(fingerprint, *, record=False, required=False):
    """Pin the reviewed CA identity; never replace an existing record.

    The record protects against accidental drift, not a malicious account owner.
    Only an explicit operator action or guarded fresh generation may record it.
    """
    marker = material_paths()[2].parent / MARKER
    expected = (fingerprint + "\n").encode("ascii")
    try:
        owned_path(marker.parent, directory=True)
        if not marker.exists() and not marker.is_symlink():
            if not record:
                if required:
                    raise nss.NSSError("Vault CA authorization missing; review and run setup --apply")
                return "unrecorded"
            fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(expected)
                stream.flush()
                os.fsync(stream.fileno())
            fd = os.open(marker.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        owned_path(marker, private=True)
        if marker.lstat().st_size != len(expected) or marker.read_bytes() != expected:
            raise nss.NSSError("Vault CA differs from its authorization record; manual review required")
        return "recorded"
    except OSError:
        raise nss.NSSError("Cannot safely access Vault CA authorization record") from None


def check(*, require_authorized=False):
    with reviewed_ca() as (public, fingerprint):
        authorization(fingerprint, required=require_authorized)
        return chromium_trust.check(public)


def install(*, require_authorized=False):
    """Explicitly authorized setup, or automatic import of a pinned CA only."""
    with reviewed_ca() as (public, fingerprint):
        authorization(fingerprint, record=not require_authorized, required=require_authorized)
        return chromium_trust.install(public)


def present(path):
    return path.exists() or path.is_symlink()


def preflight():
    """Read-only guard: an absent TLS set is allowed, never a partial set."""
    chromium_trust.selected_database()
    ca, leaf, key = material_paths()
    server_key = leaf.with_suffix('.key')
    marker = key.parent / MARKER
    paths = (ca, leaf, key, server_key, marker)
    home = Path.home()
    try:
        for path in paths:
            if not path.is_absolute() or not path.is_relative_to(home) or '..' in path.parts:
                raise nss.NSSError("Vault TLS path must be inside the user's home")
            nss.safe_meta(home, directory=True)
            current = home
            for part in path.relative_to(home).parts[:-1]:
                current /= part
                if present(current):
                    nss.safe_meta(current, directory=True)
        count = sum(present(path) for path in paths[:-1])
        if not count:
            if present(marker):
                raise nss.NSSError("Vault CA authorization exists without TLS material")
            return False
        if count != 4:
            raise nss.NSSError("Incomplete Vault TLS state; refusing replacement")
        # Container-owned server key: inspect metadata only, never read it.
        info = server_key.lstat()
        if not stat.S_ISREG(info.st_mode) or not info.st_size or info.st_nlink != 1 or info.st_mode & 0o077:
            raise nss.NSSError("Unsafe Vault server private key; manual review required")
        with reviewed_ca() as (_, fingerprint):
            authorization(fingerprint, required=True)
        return True
    except OSError:
        raise nss.NSSError("Cannot safely inspect Vault TLS state") from None


def prepare():
    """Generate only an absent set; pin fresh CA before automatic NSS import."""
    existing = preflight()
    if not existing:
        ca, _, key = material_paths()
        # Fix the reviewed paths for the existing generator. Its umask is 077.
        env = {**os.environ, 'HOME': str(Path.home()), 'FT_DEV_TLS_DIR': str(ca.parent),
               'XDG_DATA_HOME': str(key.parents[2])}
        try:
            result = subprocess.run(['bash', str(ROOT / 'infra/vault/server/tls-init.sh')],
                                    env=env, stdin=subprocess.DEVNULL, capture_output=True,
                                    check=False, timeout=90)
        except (OSError, subprocess.SubprocessError):
            raise nss.NSSError("Vault TLS generation failed; inspect local TLS state") from None
        if result.returncode:
            raise nss.NSSError("Vault TLS generation failed; existing files retained")
        with reviewed_ca() as (_, fingerprint):
            authorization(fingerprint, record=True)
    return install(require_authorized=True)


def removal(action):
    """Remove only the pinned Vault CA entry, without requiring a valid leaf."""
    ca, _, key = material_paths()
    if not present(ca) and not present(key.parent / MARKER):
        print('[OK] No local Vault CA material to remove')
        return 0
    owned_path(ca)
    public = nss.read_ca(ca)
    authorization(public.fingerprint, required=True)
    db = chromium_trust.selected_database()
    item = nss.matching(db, public)
    if item is None or item.nickname != public.nickname:
        print('[OK] No managed Vault CA entry to remove')
        return 0
    if item.trust != nss.TRUST:
        raise nss.NSSError("Managed Vault CA trust changed; refusing removal")
    print('[PLAN] Remove managed Vault CA from selected NSS database')
    if action == 'uninstall-plan':
        return 0
    from scripts.dev.ca_trust import confirm_native
    if not confirm_native('Remove this Vault CA trust from NSS? [y/N]: '):
        return 1
    if chromium_trust.selected_database() != db or nss.read_ca(ca) != public:
        raise nss.NSSError("NSS location or Vault CA changed; review before retrying")
    authorization(public.fingerprint, required=True)
    with tempfile.TemporaryDirectory(prefix='ft-vault-ca-remove-') as directory:
        snapshot = Path(directory) / 'ca.pem'
        snapshot.write_text(ssl.DER_cert_to_PEM_cert(public.der), encoding='ascii')
        print('Operation:', nss.uninstall(db, snapshot))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('check', 'setup', 'preflight', 'prepare',
                                           'check-authorized', 'uninstall-plan', 'uninstall'))
    parser.add_argument('--apply', action='store_true', help='authorize import of the reviewed local Vault CA')
    args = parser.parse_args(argv)
    if args.apply != (args.action == 'setup'):
        parser.error('setup requires --apply; other commands do not accept --apply')
    try:
        if args.action == 'preflight':
            preflight()
            return 0
        if args.action == 'prepare':
            print('Operation:', prepare())
            print('Vault CA NSS trust: trusted')
            print('Browser HTTPS readiness: requires browser verification')
            return 0
        if args.action in ('uninstall-plan', 'uninstall'):
            return removal(args.action)
        with reviewed_ca() as (public, fingerprint):
            print('Vault CA SHA-256:', fingerprint)
            status = authorization(fingerprint, record=args.action == 'setup',
                                   required=args.action == 'check-authorized')
            print('Vault CA authorization:', status)
            if args.action == 'setup':
                print('Operation:', chromium_trust.install(public))
            status = chromium_trust.check(public)
            print('Vault CA NSS trust:', status)
            print('Browser HTTPS readiness: requires browser verification')
            return 0 if status == 'trusted' else 1
    except nss.NSSError as exc:
        print('ERROR:', exc)
        return 2
    except (OSError, ValueError, subprocess.SubprocessError):
        print('ERROR: Vault CA operation failed; manual review required')
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
