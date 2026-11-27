"""NSS contract tests: real certutil, disposable databases, no browser changes."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from scripts.dev import nss_trust as nss


class ListingTests(unittest.TestCase):
    def test_parse_listing_with_spaces_and_empty_flags(self):
        raw = (b"\nCertificate Nickname             Trust Attributes\n"
               b"                                 SSL,S/MIME,JAR/XPI\n\n"
               b"Example root with spaces          CT,C,C  \n"
               b"Untrusted                         ,,\n"
               b"ft-transcendence-long-nickname C,,\n")
        self.assertEqual(nss.parse_listing(raw), {"Example root with spaces": "CT,C,C",
                                                 "Untrusted": ",,",
                                                 "ft-transcendence-long-nickname": "C,,"})

    def test_reject_bad_listing(self):
        header = b"Certificate Nickname Trust Attributes\nSSL,S/MIME,JAR/XPI\n"
        for raw in (b"", b"unexpected", header + b"unparseable\n",
                    header + b"same  C,,\nsame  ,,\n"):
            with self.subTest(raw=raw), self.assertRaises(nss.NSSError):
                nss.parse_listing(raw)

    def test_server_trust_is_not_client_or_peer_trust(self):
        for flags, expected in (("C,,", True), ("CT,C,C", True), ("T,,", False),
                                ("P,,", False), ("c,,", False), ("pC,,", False), (",C,C", False)):
            self.assertEqual(nss.Entry("x", flags, "x").server_trusted, expected)

    def test_missing_certutil_is_explicit(self):
        with patch.object(nss.shutil, "which", return_value=None):
            with self.assertRaisesRegex(nss.NSSError, "unavailable: certutil"):
                nss.certutil(Path("/unused"), "-L")


@unittest.skipUnless(os.getuid() != 0 and shutil.which("certutil") and shutil.which("openssl"),
                     "real NSS tests require an ordinary user, certutil and OpenSSL")
class RealNSSTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix=".ft-nss-test-", dir=Path.home())
        cls.addClassCleanup(cls.temp.cleanup)
        cls.root = Path(cls.temp.name)
        for name, constraint in (("ca", "CA:TRUE"), ("other", "CA:TRUE"), ("leaf", "CA:FALSE")):
            subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                            "-days", "2", "-subj", "/CN=FT NSS test " + name,
                            "-addext", "basicConstraints=critical," + constraint,
                            "-keyout", str(cls.root / (name + ".key")),
                            "-out", str(cls.root / (name + ".pem"))],
                           check=True, capture_output=True, timeout=20)
        cls.ca = cls.root / "ca.pem"
        cls.other = cls.root / "other.pem"
        # A separate TLS server certificate signed by the first test CA.
        subprocess.run(["openssl", "req", "-new", "-key", str(cls.root / "leaf.key"),
                        "-subj", "/CN=localhost", "-out", str(cls.root / "server.csr")],
                       check=True, capture_output=True, timeout=20)
        extensions = cls.root / "server.ext"
        extensions.write_text("basicConstraints=critical,CA:FALSE\n"
                              "keyUsage=critical,digitalSignature,keyEncipherment\n"
                              "extendedKeyUsage=serverAuth\nsubjectAltName=DNS:localhost\n")
        subprocess.run(["openssl", "x509", "-req", "-in", str(cls.root / "server.csr"),
                        "-CA", str(cls.ca), "-CAkey", str(cls.root / "ca.key"), "-set_serial", "1",
                        "-days", "1", "-extfile", str(extensions),
                        "-out", str(cls.root / "server.pem")],
                       check=True, capture_output=True, timeout=20)

    def setUp(self):
        temp = tempfile.TemporaryDirectory(dir=self.root)
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / "nssdb"

    def init(self):
        self.path.mkdir(mode=0o700)
        nss.certutil(self.path, "-N", "--empty-password")

    def add(self, cert, nickname, trust):
        nss.certutil(self.path, "-A", "-n", nickname, "-t", trust,
                     data=nss.read_ca(cert).der)

    def test_missing_check_does_not_create_database(self):
        self.assertEqual(nss.check(self.path, self.ca), "missing")
        self.assertFalse(self.path.exists())
        self.assertEqual(nss.uninstall(self.path, self.ca), "unchanged")
        self.assertFalse(self.path.exists())

    def test_install_idempotence_and_remove_preserves_other_certificate(self):
        self.assertEqual(nss.install(self.path, self.ca), "installed")
        self.add(self.other, "unrelated root", "CT,C,C")
        original = nss.entries(self.path)
        self.assertEqual(nss.check(self.path, self.ca), "trusted")
        self.assertEqual(nss.install(self.path, self.ca), "unchanged")
        self.assertEqual(nss.entries(self.path), original)
        self.assertEqual(nss.uninstall(self.path, self.ca), "removed")
        self.assertEqual(nss.uninstall(self.path, self.ca), "unchanged")
        self.assertEqual(nss.check(self.path, self.ca), "missing")
        self.assertEqual(nss.entries(self.path), [item for item in original if item.nickname == "unrelated root"])
        self.assertTrue(all((self.path / name).exists() for name in nss.DATABASE_FILES))

    def test_nss_server_chain_rejects_wrong_ca_and_accepts_correct_ca(self):
        nss.install(self.path, self.other)
        self.add(self.root / "server.pem", "TLS server", ",,")
        with self.assertRaises(nss.NSSError):
            nss.certutil(self.path, "-V", "-n", "TLS server", "-u", "V")
        nss.install(self.path, self.ca)
        nss.certutil(self.path, "-V", "-n", "TLS server", "-u", "V")
        nss.uninstall(self.path, self.ca)
        with self.assertRaises(nss.NSSError):
            nss.certutil(self.path, "-V", "-n", "TLS server", "-u", "V")
        self.assertEqual(nss.check(self.path, self.other), "trusted")

    def test_password_protected_database_not_reset(self):
        self.path.mkdir(mode=0o700)
        password = self.path.parent / "test-password"
        password.write_text("test-only-nss-password\n")
        nss.certutil(self.path, "-N", "-f", str(password))
        key_db = self.path / "key4.db"
        original = key_db.read_bytes()
        with self.assertRaises(nss.NSSError):
            nss.install(self.path, self.ca)
        self.assertEqual(key_db.read_bytes(), original)
        self.assertEqual(nss.check(self.path, self.ca), "missing")

    def test_existing_trusted_alias_is_preserved(self):
        self.init()
        self.add(self.ca, "existing external CA", "CT,C,C")
        original = nss.entries(self.path)
        self.assertEqual(nss.install(self.path, self.ca), "unchanged")
        self.assertEqual(nss.uninstall(self.path, self.ca), "unchanged")
        self.assertEqual(nss.entries(self.path), original)

    def test_existing_untrusted_alias_is_not_overwritten(self):
        self.init()
        self.add(self.ca, "external CA", ",,")
        original = nss.entries(self.path)
        self.assertEqual(nss.check(self.path, self.ca), "untrusted")
        with self.assertRaisesRegex(nss.NSSError, "unexpected trust"):
            nss.install(self.path, self.ca)
        self.assertEqual(nss.entries(self.path), original)

    def test_managed_nickname_collision_refused(self):
        self.init()
        self.add(self.other, nss.read_ca(self.ca).nickname, "C,,")
        original = nss.entries(self.path)
        for operation in (nss.check, nss.install, nss.uninstall):
            with self.assertRaisesRegex(nss.NSSError, "collision"):
                operation(self.path, self.ca)
        self.assertEqual(nss.entries(self.path), original)

    def test_changed_managed_flags_are_not_silently_fixed_or_removed(self):
        nss.install(self.path, self.ca)
        nss.certutil(self.path, "-M", "-n", nss.read_ca(self.ca).nickname, "-t", "CT,C,C")
        self.assertEqual(nss.check(self.path, self.ca), "untrusted")
        for operation in (nss.install, nss.uninstall):
            with self.assertRaises(nss.NSSError):
                operation(self.path, self.ca)

    def test_partial_legacy_and_unknown_nonempty_database_not_initialized(self):
        self.path.mkdir()
        for name in ("cert9.db", "cert8.db", "unknown"):
            file = self.path / name
            file.write_bytes(b"preserve")
            with self.assertRaisesRegex(nss.NSSError, "Incomplete or legacy"):
                nss.install(self.path, self.ca)
            self.assertEqual(file.read_bytes(), b"preserve")
            file.unlink()

    def test_database_directory_and_file_symlinks_refused(self):
        target = self.path.parent / "target"
        target.mkdir()
        self.path.symlink_to(target, target_is_directory=True)
        with self.assertRaises(nss.NSSError):
            nss.install(self.path, self.ca)
        self.assertEqual(list(target.iterdir()), [])
        self.path.unlink()
        self.init()
        (self.path / "cert9.db").rename(target / "cert9.db")
        (self.path / "cert9.db").symlink_to(target / "cert9.db")
        with self.assertRaises(nss.NSSError):
            nss.check(self.path, self.ca)

    def test_world_writable_directory_refused(self):
        self.path.mkdir(mode=0o700)
        self.path.chmod(0o777)
        with self.assertRaises(nss.NSSError):
            nss.install(self.path, self.ca)
        self.assertEqual(list(self.path.iterdir()), [])

    def test_reject_leaf_private_key_bundle_and_symlink(self):
        bundle = self.path.parent / "bundle.pem"
        bundle.write_bytes(self.ca.read_bytes() + self.other.read_bytes())
        link = self.path.parent / "link.pem"
        link.symlink_to(self.ca)
        for file in (self.root / "leaf.pem", self.root / "ca.key", bundle, link):
            with self.subTest(file=file.name), self.assertRaises(nss.NSSError):
                nss.install(self.path, file)
        self.assertFalse(self.path.exists())

    def test_outside_home_refused(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as directory:
            with self.assertRaises(nss.NSSError):
                nss.install(Path(directory) / "nssdb", self.ca)


if __name__ == "__main__":
    unittest.main()
