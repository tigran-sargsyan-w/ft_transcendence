"""Native application trust transition, with real TLS and disposable NSS stores."""
import io
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from contextlib import ExitStack, redirect_stdout
from unittest.mock import patch

from scripts.dev import bootstrap_linux_ca as guard, ca_trust, certificates, chromium_trust, nss_trust


@unittest.skipUnless(os.getuid() != 0 and shutil.which("openssl") and shutil.which("certutil"),
                     "native NSS integration requires an ordinary user, OpenSSL and certutil")
class NativeTrustTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.fixture = Path(cls.temp.name)
        def run(*args):
            subprocess.run(["openssl", *args], cwd=cls.fixture, capture_output=True,
                           check=True, timeout=20)
        run("req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "90",
            "-subj", "/CN=FT isolated application CA", "-addext", "basicConstraints=critical,CA:TRUE",
            "-keyout", "rootCA-key.pem", "-out", "rootCA.pem")
        run("req", "-new", "-newkey", "rsa:2048", "-nodes", "-subj", "/CN=localhost",
            "-keyout", "key.pem", "-out", "leaf.csr")
        (cls.fixture / "leaf.ext").write_text("basicConstraints=critical,CA:FALSE\n"
            "extendedKeyUsage=serverAuth\nsubjectAltName=DNS:localhost,IP:127.0.0.1\n")
        run("x509", "-req", "-in", "leaf.csr", "-CA", "rootCA.pem", "-CAkey", "rootCA-key.pem",
            "-set_serial", "1", "-days", "60", "-extfile", "leaf.ext", "-out", "cert.pem")

    def setUp(self):
        self.temp_home = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_home.cleanup)
        self.home = Path(self.temp_home.name)
        self.root = self.home / ".local/share/mkcert"
        self.public = self.root / "rootCA.pem"
        self.db = self.home / ".local/share/pki/nssdb"
        self.leaf = self.home / "cert.pem"
        self.key = self.home / "key.pem"
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.dict(os.environ, {"HOME": str(self.home)}, clear=False))
        for key in ("XDG_DATA_HOME", "CAROOT", "DEV_DOMAIN"):
            os.environ.pop(key, None)
        self.stack.enter_context(patch.object(certificates, "get_ca_root", return_value=self.root))
        self.stack.enter_context(patch.object(certificates, "CERT_FILE", self.leaf))
        self.stack.enter_context(patch.object(certificates, "KEY_FILE", self.key))
        self.output = io.StringIO()
        self.stack.enter_context(redirect_stdout(self.output))

    def material(self):
        # Reproduce secure generated material independently of the caller's umask.
        # mkdir(parents=True) does not apply mode to intermediate directories.
        for directory in (self.home / ".local", self.home / ".local/share", self.root):
            directory.mkdir(mode=0o700)
        for name in ("rootCA.pem", "rootCA-key.pem"):
            shutil.copy2(self.fixture / name, self.root / name)
        shutil.copy2(self.fixture / "cert.pem", self.leaf)
        shutil.copy2(self.fixture / "key.pem", self.key)
        self.public.chmod(0o644)
        (self.root / "rootCA-key.pem").chmod(0o600)
        self.leaf.chmod(0o644)
        self.key.chmod(0o600)
        self.assertFalse(ca_trust.check_linux(self.public))

    def authorize(self):
        guard.record_fingerprint(self.root, guard.fingerprint(self.root))

    def test_explicit_setup_works_without_system_trust_and_is_idempotent(self):
        self.material()
        self.assertEqual(ca_trust.inspect("check"), 1)
        self.assertFalse(self.db.exists())
        self.assertFalse((self.root / guard.MARKER).exists())
        with patch("builtins.input", return_value="y") as confirm, \
                patch.object(ca_trust, "check_linux", side_effect=AssertionError("system trust not required")):
            self.assertEqual(ca_trust.setup(), 0)
            self.assertEqual(ca_trust.inspect("check"), 0)
            self.assertEqual(ca_trust.setup(), 0)
        confirm.assert_called_once()
        self.assertEqual(chromium_trust.check(self.public), "trusted")
        self.assertFalse(ca_trust.check_linux(self.public))
        self.assertIn("Browser HTTPS readiness: requires browser verification", self.output.getvalue())

    def test_existing_nss_trust_is_not_automatic_authorization(self):
        self.material()
        chromium_trust.install(self.public)
        self.assertEqual(ca_trust.inspect("check"), 1)
        self.assertEqual(ca_trust.install_linux(self.public), 2)
        with patch("builtins.input", return_value="n"):
            self.assertEqual(ca_trust.setup_linux(), 1)
        self.assertFalse((self.root / guard.MARKER).exists())

    def test_mismatched_record_refused_even_on_explicit_setup(self):
        self.material()
        guard.record_fingerprint(self.root, "0" * 64)
        with patch("builtins.input") as confirm:
            self.assertEqual(ca_trust.setup(), 2)
            self.assertEqual(ca_trust.install_linux(self.public), 2)
        confirm.assert_not_called()
        self.assertFalse(self.db.exists())
        self.assertEqual((self.root / guard.MARKER).read_text(), "0" * 64 + "\n")

    def test_automatic_prepare_pins_fresh_ca_and_imports_without_system_trust(self):
        # Simulate only the mkcert generation step using genuine TLS material.
        def generate():
            self.material()
            return 0
        with patch.object(guard, "guard_existing", return_value=(self.root, None)), \
                patch.object(certificates, "setup", side_effect=generate), \
                patch.object(guard.nginx_tls_access, "check_files"), \
                patch("builtins.input", side_effect=AssertionError("unexpected confirmation")):
            self.assertEqual(guard.prepare(), 0)
        self.assertTrue(guard.verify_record(self.root, guard.fingerprint(self.root)))
        self.assertEqual(ca_trust.inspect("check"), 0)
        self.assertFalse(ca_trust.check_linux(self.public))

    def test_cached_ca_preparation_never_skips_nss_when_system_trusted(self):
        self.material()
        self.authorize()
        with patch.object(guard, "guard_existing", return_value=(self.root, guard.fingerprint(self.root))), \
                patch.object(certificates, "setup", return_value=0), \
                patch.object(guard.nginx_tls_access, "check_files"), \
                patch.object(ca_trust, "check_linux", return_value=True) as system:
            self.assertEqual(guard.prepare(), 0)
        system.assert_not_called()
        self.assertEqual(chromium_trust.check(self.public), "trusted")

    def test_uninstall_plan_and_removal_preserve_files_and_other_entries(self):
        self.material()
        self.authorize()
        self.assertEqual(ca_trust.install_linux(self.public), 0)
        # An unrelated NSS certificate, here the TLS leaf with no CA trust.
        nss_trust.certutil(self.db, "-A", "-n", "other certificate", "-t", ",,",
                          data=nss_trust.read_ca(self.leaf).der)
        before = nss_trust.entries(self.db)
        files = [self.public, self.root / "rootCA-key.pem", self.root / guard.MARKER, self.leaf, self.key]
        original = [p.read_bytes() for p in files]
        self.assertEqual(ca_trust.uninstall_plan(), 0)
        self.assertEqual(nss_trust.entries(self.db), before)
        with patch("builtins.input", return_value="y") as confirm:
            self.assertEqual(ca_trust.uninstall(), 0)
            self.assertEqual(ca_trust.uninstall(), 0)
        confirm.assert_called_once()
        self.assertEqual(nss_trust.entries(self.db), [e for e in before if e.nickname == "other certificate"])
        self.assertEqual([p.read_bytes() for p in files], original)
        self.assertEqual(ca_trust.inspect("check"), 1)

    def test_no_installed_browser_is_explicit_not_a_false_browser_pass(self):
        self.material()
        self.authorize()
        with patch.object(chromium_trust, "installed_browsers", return_value=()):
            self.assertEqual(ca_trust.install_linux(self.public), 0)
        self.assertIn("Detected browsers: none", self.output.getvalue())
        self.assertIn("Browser HTTPS readiness: requires browser verification", self.output.getvalue())


if __name__ == "__main__":
    unittest.main()
