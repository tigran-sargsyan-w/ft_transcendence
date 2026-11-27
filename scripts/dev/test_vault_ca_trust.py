"""Vault public CA integration using disposable local TLS and NSS stores."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from scripts.dev import chromium_trust, nss_trust as nss, vault_ca_trust as trust


@unittest.skipUnless(os.getuid() != 0 and shutil.which('certutil') and shutil.which('openssl'),
                     'real Vault CA tests require ordinary user, certutil and OpenSSL')
class VaultCATests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temp = tempfile.TemporaryDirectory(prefix='.ft-vault-ca-test-', dir=Path.home())
        cls.addClassCleanup(temp.cleanup)
        cls.root = Path(temp.name)
        cls.fixture = cls.root / 'fixture'
        cls.fixture.mkdir()
        root = Path(__file__).resolve().parents[2]
        env = {**os.environ, 'HOME': str(cls.fixture), 'XDG_STATE_HOME': '', 'XDG_DATA_HOME': ''}
        env.update(XDG_STATE_HOME=str(cls.fixture / '.local/state'),
                   XDG_DATA_HOME=str(cls.fixture / '.local/share'),
                   FT_DEV_TLS_DIR=str(cls.fixture / '.local/state/transcendence/dev/tls'))
        subprocess.run(['bash', str(root / 'infra/vault/server/tls-init.sh')],
                       env=env, check=True, capture_output=True, timeout=30)
        cls.other = cls.root / 'other.pem'
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '2',
                        '-subj', '/CN=Application test CA', '-addext', 'basicConstraints=critical,CA:TRUE',
                        '-keyout', str(cls.root / 'other.key'), '-out', str(cls.other)],
                       check=True, capture_output=True, timeout=20)

    def setUp(self):
        temp = tempfile.TemporaryDirectory(dir=self.root)
        self.addCleanup(temp.cleanup)
        self.home = Path(temp.name)
        shutil.copytree(self.fixture, self.home, dirs_exist_ok=True)
        for context in (patch.object(Path, 'home', return_value=self.home),
                        patch.dict(os.environ, {'XDG_STATE_HOME': '', 'XDG_DATA_HOME': '', 'FT_DEV_TLS_DIR': ''})):
            context.start()
            self.addCleanup(context.stop)
        self.ca, self.leaf, self.key = trust.material_paths()
        self.db = chromium_trust.selected_database()

    def test_check_is_read_only_and_install_is_idempotent(self):
        self.assertEqual(trust.check(), 'missing')
        self.assertFalse(self.db.exists())
        self.assertEqual(trust.install(), 'installed')
        self.assertEqual(trust.check(), 'trusted')
        self.assertEqual(trust.install(), 'unchanged')

    def test_preserves_application_ca_and_tls_files(self):
        chromium_trust.install(self.other)
        before = {p: p.read_bytes() for p in (self.ca, self.leaf, self.key)}
        self.assertEqual(trust.install(), 'installed')
        self.assertEqual(chromium_trust.check(self.other), 'trusted')
        self.assertEqual(len(nss.entries(self.db)), 2)
        self.assertEqual(before, {p: p.read_bytes() for p in before})

    def test_server_private_key_is_not_read(self):
        key = self.leaf.with_suffix('.key')
        key.chmod(0)
        try:
            self.assertEqual(trust.install(), 'installed')
        finally:
            key.chmod(0o600)

    def owner_metadata(self, target, uid):
        original = Path.lstat

        def lstat(path, *args, **kwargs):
            info = original(path, *args, **kwargs)
            if path == target:
                fields = list(info)
                fields[4] = uid
                return os.stat_result(fields)
            return info

        return patch.object(Path, 'lstat', lstat)

    def test_root_owned_public_leaf_is_accepted(self):
        with self.owner_metadata(self.leaf, 0):
            self.assertEqual(trust.check(), 'missing')
            self.assertEqual(trust.install(), 'installed')
            self.assertEqual(trust.check(), 'trusted')
            self.assertEqual(trust.install(), 'unchanged')
            # The generic NSS certificate reader still refuses root ownership.
            with self.assertRaises(nss.NSSError):
                nss.read_ca(self.leaf)

    def test_root_owned_writable_leaf_is_refused(self):
        for mode in (0o664, 0o666):
            with self.subTest(mode=mode):
                self.leaf.chmod(mode)
                with self.owner_metadata(self.leaf, 0), self.assertRaises(nss.NSSError):
                    trust.install()
                self.assertFalse(self.db.exists())

    def test_foreign_owned_leaf_is_refused(self):
        with self.owner_metadata(self.leaf, os.getuid() + 1), self.assertRaises(nss.NSSError):
            trust.install()
        self.assertFalse(self.db.exists())

    def test_root_ownership_exception_does_not_extend_to_ca_or_key(self):
        for path in (self.ca, self.key, self.leaf.parent):
            with self.subTest(path=path):
                with self.owner_metadata(path, 0), self.assertRaises(nss.NSSError):
                    trust.install()
                self.assertFalse(self.db.exists())

    def test_wrong_ca_key_is_refused_before_import(self):
        self.key.write_bytes((self.root / 'other.key').read_bytes())
        with self.assertRaisesRegex(nss.NSSError, 'do not match'):
            trust.install()
        self.assertFalse(self.db.exists())

    def test_other_ca_is_refused_before_import(self):
        self.ca.write_bytes(self.other.read_bytes())
        with self.assertRaisesRegex(nss.NSSError, 'subject'):
            trust.install()
        self.assertFalse(self.db.exists())

    def test_wrong_leaf_refused_before_import(self):
        self.leaf.write_bytes(self.other.read_bytes())
        with self.assertRaises(nss.NSSError):
            trust.install()
        self.assertFalse(self.db.exists())

    def test_leaf_with_correct_sans_but_wrong_issuer_is_refused(self):
        csr = self.home / 'server.csr'
        extensions = self.home / 'server.ext'
        extensions.write_text('subjectAltName=DNS:vault,DNS:localhost,IP:127.0.0.1\n'
                              'basicConstraints=critical,CA:FALSE\n'
                              'extendedKeyUsage=serverAuth\n')
        subprocess.run(['openssl', 'req', '-new', '-key', str(self.leaf.with_suffix('.key')),
                        '-subj', '/CN=vault', '-out', str(csr)],
                       check=True, capture_output=True, timeout=20)
        subprocess.run(['openssl', 'x509', '-req', '-in', str(csr), '-CA', str(self.other),
                        '-CAkey', str(self.root / 'other.key'), '-set_serial', '1',
                        '-days', '1', '-extfile', str(extensions), '-out', str(self.leaf)],
                       check=True, capture_output=True, timeout=20)
        with self.assertRaises(nss.NSSError):
            trust.install()
        self.assertFalse(self.db.exists())

    def test_unsafe_permissions_and_symlinks_refused(self):
        self.key.chmod(0o644)
        with self.assertRaisesRegex(nss.NSSError, 'permissions'):
            trust.install()
        self.key.chmod(0o600)
        self.ca.unlink()
        self.ca.symlink_to(self.other)
        with self.assertRaises(nss.NSSError):
            trust.install()
        self.assertFalse(self.db.exists())

    def test_missing_material_and_override_refused(self):
        with patch.dict(os.environ, {'FT_DEV_TLS_DIR': '/tmp/other-tls'}):
            with self.assertRaisesRegex(nss.NSSError, 'override'):
                trust.install()
        self.leaf.unlink()
        with self.assertRaises(nss.NSSError):
            trust.install()
        self.assertFalse(self.db.exists())

    def test_automatic_import_requires_authorization_even_when_nss_trusted(self):
        chromium_trust.install(self.ca)
        marker = self.key.parent / trust.MARKER
        with self.assertRaisesRegex(nss.NSSError, 'authorization missing'):
            trust.install(require_authorized=True)
        with self.assertRaisesRegex(nss.NSSError, 'authorization missing'):
            trust.check(require_authorized=True)
        self.assertFalse(marker.exists())
        self.assertEqual(trust.install(), 'unchanged')
        self.assertEqual(marker.stat().st_mode & 0o777, 0o600)
        before = marker.stat().st_mtime_ns
        self.assertEqual(trust.install(require_authorized=True), 'unchanged')
        self.assertEqual(trust.check(require_authorized=True), 'trusted')
        self.assertEqual(marker.stat().st_mtime_ns, before)

    def test_authorized_ca_can_be_imported_into_fresh_database(self):
        self.assertEqual(trust.install(), 'installed')
        shutil.rmtree(self.db)
        self.assertEqual(trust.check(require_authorized=True), 'missing')
        self.assertFalse(self.db.exists())
        self.assertEqual(trust.install(require_authorized=True), 'installed')

    def test_mismatched_authorization_is_never_replaced(self):
        marker = self.key.parent / trust.MARKER
        marker.write_text('0' * 64 + '\n')
        marker.chmod(0o600)
        before = marker.read_bytes()
        for operation in (trust.check, trust.install,
                          lambda: trust.install(require_authorized=True)):
            with self.assertRaisesRegex(nss.NSSError, 'differs'):
                operation()
        self.assertEqual(marker.read_bytes(), before)
        self.assertFalse(self.db.exists())

    def test_unsafe_authorization_record_is_refused(self):
        marker = self.key.parent / trust.MARKER
        marker.symlink_to(self.ca)
        with self.assertRaises(nss.NSSError):
            trust.install()
        marker.unlink()
        marker.write_text('0' * 64 + '\n')
        marker.chmod(0o644)
        with self.assertRaises(nss.NSSError):
            trust.install()
        self.assertFalse(self.db.exists())

    def test_cli_requires_explicit_apply(self):
        with self.assertRaises(SystemExit) as ctx:
            trust.main(['setup'])
        self.assertEqual(ctx.exception.code, 2)
        self.assertFalse(self.db.exists())


if __name__ == '__main__':
    unittest.main()
