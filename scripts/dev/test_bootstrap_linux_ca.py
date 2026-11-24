"""Offline tests for automatic Linux CA trust decisions."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.dev import bootstrap_linux_ca as ca


class LinuxCATests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.cert = self.root / "rootCA.pem"
        self.key = self.root / "rootCA-key.pem"

    def pair(self):
        self.cert.write_text("test certificate")
        self.key.write_text("test key")
        os.chmod(self.key, 0o600)

    def test_absent_is_allowed(self):
        self.assertIsNone(ca.inspect_ca(self.root))

    def test_partial_is_refused(self):
        self.cert.touch()
        with self.assertRaisesRegex(ca.LinuxCAError, "Incomplete"):
            ca.inspect_ca(self.root)

    def test_unknown_untrusted_ca_is_refused(self):
        self.pair()
        with patch.object(ca, "fingerprint", return_value="a"*64), \
             patch.object(ca.ca_trust, "check_linux", return_value=False):
            with self.assertRaisesRegex(ca.LinuxCAError, "Unknown untrusted"):
                ca.inspect_ca(self.root)

    def test_existing_trusted_ca_is_kept(self):
        self.pair()
        with patch.object(ca, "fingerprint", return_value="a"*64), \
             patch.object(ca.ca_trust, "check_linux", return_value=True):
            self.assertEqual(ca.inspect_ca(self.root), "a"*64)

    def test_recorded_ca_can_resume(self):
        self.pair()
        ca.record_fingerprint(self.root, "a"*64)
        with patch.object(ca, "fingerprint", return_value="a"*64), \
             patch.object(ca.ca_trust, "check_linux") as trust:
            self.assertEqual(ca.inspect_ca(self.root), "a"*64)
            trust.assert_not_called()

    def test_changed_ca_is_refused(self):
        self.pair()
        ca.record_fingerprint(self.root, "a"*64)
        with patch.object(ca, "fingerprint", return_value="b"*64):
            with self.assertRaisesRegex(ca.LinuxCAError, "differs"):
                ca.inspect_ca(self.root)

    def test_missing_ca_with_existing_browser_pair_is_refused(self):
        self.pair()
        with patch.object(ca, "native_linux"), \
             patch.object(ca, "ca_root", return_value=self.root), \
             patch.object(ca, "inspect_ca", return_value=None), \
             patch.object(ca.linux_hosts, "read_hosts", return_value=""), \
             patch.object(ca.linux_hosts, "append_text", return_value=""), \
             patch.object(ca.certificates, "CERT_FILE", self.cert), \
             patch.object(ca.certificates, "KEY_FILE", self.key):
            with self.assertRaisesRegex(ca.LinuxCAError, "Incomplete browser TLS"):
                ca.guard_existing()

    def test_private_symlink_is_refused(self):
        self.pair()
        link = self.root / "link"
        link.symlink_to(self.key)
        with self.assertRaises(ca.LinuxCAError):
            ca.safe_file(link, private=True)


if __name__ == "__main__":
    unittest.main()
