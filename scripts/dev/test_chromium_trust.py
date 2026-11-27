"""Shared NSS selection and real CA integration on disposable home paths."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from scripts.dev import chromium_trust as browser, nss_trust as nss


class DiscoveryTests(unittest.TestCase):
    def test_executable_discovery_is_not_browser_verification(self):
        commands = {"google-chrome-stable", "opera"}
        with patch.object(browser.shutil, "which", side_effect=lambda name: name if name in commands else None):
            self.assertEqual(browser.installed_browsers(), ("Chrome", "Opera"))
        with patch.object(browser.shutil, "which", return_value=None):
            self.assertEqual(browser.installed_browsers(), ())

    def test_non_native_linux_refused(self):
        for system, release in (("Windows", ""), ("Linux", "WSL2-microsoft-standard")):
            with self.subTest(system=system, release=release), \
                    patch.object(browser.platform, "system", return_value=system), \
                    patch.object(browser.platform, "release", return_value=release), \
                    patch.object(nss, "database_state") as state:
                with self.assertRaises(nss.NSSError):
                    browser.selected_database()
                state.assert_not_called()

    def test_location_drift_during_import_is_explicit(self):
        with patch.object(browser, "selected_database", side_effect=[Path("/first"), Path("/second")]), \
                patch.object(nss, "install", return_value="installed"), \
                patch.object(nss, "check") as check:
            with self.assertRaisesRegex(nss.NSSError, "location changed"):
                browser.install(Path("ca.pem"))
            check.assert_not_called()


@unittest.skipUnless(os.getuid() != 0 and shutil.which("certutil") and shutil.which("openssl"),
                     "real shared NSS tests require an ordinary user, certutil and OpenSSL")
class SharedDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temp = tempfile.TemporaryDirectory(prefix=".ft-chromium-test-", dir=Path.home())
        cls.addClassCleanup(temp.cleanup)
        cls.root = Path(temp.name)
        cls.ca = cls.root / "ca.pem"
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2",
                        "-subj", "/CN=Shared NSS test CA", "-addext", "basicConstraints=critical,CA:TRUE",
                        "-keyout", str(cls.root / "ca.key"), "-out", str(cls.ca)],
                       check=True, capture_output=True, timeout=20)

    def setUp(self):
        temp = tempfile.TemporaryDirectory(dir=self.root)
        self.addCleanup(temp.cleanup)
        self.home = Path(temp.name)
        self.legacy = self.home / ".pki/nssdb"
        self.modern = self.home / ".local/share/pki/nssdb"
        for context in (patch.object(Path, "home", return_value=self.home),
                        patch.dict(os.environ, {"XDG_DATA_HOME": ""})):
            context.start()
            self.addCleanup(context.stop)

    def test_fresh_check_and_install_use_only_modern_store(self):
        self.assertEqual(browser.selected_database(), self.modern)
        self.assertEqual(browser.check(self.ca), "missing")
        self.assertEqual(list(self.home.iterdir()), [])
        self.assertEqual(browser.install(self.ca), "installed")
        self.assertEqual(browser.check(self.ca), "trusted")
        self.assertEqual(browser.install(self.ca), "unchanged")
        self.assertFalse(self.legacy.parent.exists())

    def test_existing_empty_legacy_directory_takes_precedence(self):
        self.legacy.mkdir(parents=True, mode=0o700)
        self.assertEqual(browser.selected_database(), self.legacy)
        self.assertEqual(browser.install(self.ca), "installed")
        self.assertFalse(self.modern.parent.exists())

    def test_two_existing_databases_only_selected_one_is_modified(self):
        for path in (self.modern, self.legacy):
            path.mkdir(parents=True, mode=0o700)
            nss.certutil(path, "-N", "--empty-password")
        self.assertEqual(browser.selected_database(), self.legacy)
        self.assertEqual(browser.install(self.ca), "installed")
        self.assertEqual(nss.check(self.legacy, self.ca), "trusted")
        self.assertEqual(nss.check(self.modern, self.ca), "missing")

    def test_xdg_data_home_inside_home(self):
        data = self.home / "custom-data"
        with patch.dict(os.environ, {"XDG_DATA_HOME": str(data)}):
            self.assertEqual(browser.selected_database(), data / "pki/nssdb")
            self.assertEqual(browser.install(self.ca), "installed")
        self.assertFalse(self.modern.exists())
        self.assertFalse(self.legacy.exists())

    def test_unsafe_xdg_path_refused_without_fallback(self):
        for data in ("relative-data", "/tmp/outside-data", str(self.home / ".." / "other")):
            with self.subTest(data=data), patch.dict(os.environ, {"XDG_DATA_HOME": data}):
                with self.assertRaises(nss.NSSError):
                    browser.install(self.ca)
        self.assertEqual(list(self.home.iterdir()), [])

    def test_partial_legacy_database_refused_without_modern_fallback(self):
        self.legacy.mkdir(parents=True, mode=0o700)
        file = self.legacy / "cert9.db"
        file.write_bytes(b"preserve")
        with self.assertRaises(nss.NSSError):
            browser.install(self.ca)
        self.assertEqual(file.read_bytes(), b"preserve")
        self.assertFalse(self.modern.exists())

    def test_legacy_symlink_refused_without_modern_fallback(self):
        self.legacy.parent.mkdir()
        target = self.home / "target"
        target.mkdir()
        self.legacy.symlink_to(target, target_is_directory=True)
        with self.assertRaises(nss.NSSError):
            browser.install(self.ca)
        self.assertEqual(list(target.iterdir()), [])
        self.assertFalse(self.modern.exists())

    def test_modern_symlink_refused(self):
        self.modern.parent.mkdir(parents=True)
        self.modern.symlink_to(self.home / "absent")
        with self.assertRaises(nss.NSSError):
            browser.install(self.ca)
        self.assertFalse(self.legacy.exists())


if __name__ == "__main__":
    unittest.main()
