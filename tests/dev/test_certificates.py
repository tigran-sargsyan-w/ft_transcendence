
"""Tests for local TLS certificate diagnostics."""

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from scripts.dev import certificates


class CertificateDiagnosticsTests(unittest.TestCase):

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

        root = Path(self.temp.name)

        self.ca_root = root / "ca"
        self.ca_root.mkdir()

        self.cert_file = root / "certs" / "cert.pem"
        self.key_file = root / "certs" / "key.pem"
        self.cert_file.parent.mkdir()

        patches = [
            patch.object(
                certificates, "CERT_FILE", self.cert_file
            ),
            patch.object(
                certificates, "KEY_FILE", self.key_file
            ),
            patch.object(
                certificates,
                "get_ca_root",
                return_value=self.ca_root,
            ),
        ]

        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def run_check(self):
        with redirect_stdout(io.StringIO()):
            return certificates.check()

    def create_file(self, path):
        path.write_text("test", encoding="utf-8")

    def test_all_files_missing(self):
        self.assertEqual(self.run_check(), 1)

    def test_only_ca_exists(self):
        self.create_file(self.ca_root / "rootCA.pem")
        self.create_file(self.ca_root / "rootCA-key.pem")

        self.assertEqual(self.run_check(), 1)

    def test_all_files_exist(self):
        for path in [
            self.ca_root / "rootCA.pem",
            self.ca_root / "rootCA-key.pem",
            self.cert_file,
            self.key_file,
        ]:
            self.create_file(path)

        # This test checks file presence, not cryptography.
        with patch.object(
            certificates,
            "validate_certificate",
            return_value=(
                True,
                "Certificate cryptographically valid",
            ),
        ):
            self.assertEqual(self.run_check(), 0)

    def test_missing_private_key(self):
        for path in [
            self.ca_root / "rootCA.pem",
            self.ca_root / "rootCA-key.pem",
            self.cert_file,
        ]:
            self.create_file(path)

        self.assertEqual(self.run_check(), 1)

    def test_ca_root_detection_failure(self):
        with patch.object(
            certificates,
            "get_ca_root",
            return_value=None,
        ):
            self.assertEqual(self.run_check(), 2)

    def test_invalid_certificate_is_rejected(self):
        # All files exist, but contain invalid PEM data.
        for path in [
            self.ca_root / "rootCA.pem",
            self.ca_root / "rootCA-key.pem",
            self.cert_file,
            self.key_file,
        ]:
            self.create_file(path)

        # Real OpenSSL validation must reject these files.
        self.assertEqual(self.run_check(), 1)


if __name__ == "__main__":
    unittest.main()
