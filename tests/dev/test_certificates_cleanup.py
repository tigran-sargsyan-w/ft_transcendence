
"""Tests for TLS certificate cleanup."""

import io
import tempfile
import unittest

from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from scripts.dev import certificates


class CertificateCleanupTests(unittest.TestCase):

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

        root = Path(self.temp.name)

        self.cert_dir = root / "certs"
        self.cert_dir.mkdir()

        self.cert_file = self.cert_dir / "cert.pem"
        self.key_file = self.cert_dir / "key.pem"

        self.ca_dir = root / "ca"
        self.ca_dir.mkdir()

        self.ca_file = self.ca_dir / "rootCA.pem"
        self.ca_key = self.ca_dir / "rootCA-key.pem"

        self.ca_file.write_text("CA certificate")
        self.ca_key.write_text("CA private key")

        patches = [
            patch.object(
                certificates, "CERT_DIR", self.cert_dir
            ),
            patch.object(
                certificates, "CERT_FILE", self.cert_file
            ),
            patch.object(
                certificates, "KEY_FILE", self.key_file
            ),
        ]

        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def create_certificates(self):
        self.cert_file.write_text("TLS certificate")
        self.key_file.write_text("TLS private key")

    def run_cleanup(self, apply=False):
        with redirect_stdout(io.StringIO()):
            return certificates.cleanup(apply=apply)

    def test_cleanup_without_files(self):
        self.assertEqual(self.run_cleanup(), 0)

    def test_preview_preserves_files(self):
        self.create_certificates()

        self.assertEqual(self.run_cleanup(), 1)

        self.assertTrue(self.cert_file.exists())
        self.assertTrue(self.key_file.exists())

    def test_cleanup_declined(self):
        self.create_certificates()

        with patch("builtins.input", return_value="n"):
            result = self.run_cleanup(apply=True)

        self.assertEqual(result, 1)

        self.assertTrue(self.cert_file.exists())
        self.assertTrue(self.key_file.exists())

    def test_cleanup_removes_only_project_files(self):
        self.create_certificates()

        with patch("builtins.input", return_value="y"):
            result = self.run_cleanup(apply=True)

        self.assertEqual(result, 0)

        self.assertFalse(self.cert_file.exists())
        self.assertFalse(self.key_file.exists())

        # Root CA must remain untouched.
        self.assertTrue(self.ca_file.exists())
        self.assertTrue(self.ca_key.exists())

    def test_cleanup_idempotency(self):
        self.create_certificates()

        with patch("builtins.input", return_value="y"):
            self.assertEqual(
                self.run_cleanup(apply=True), 0
            )

        # Second cleanup must be a no-op.
        self.assertEqual(self.run_cleanup(), 0)


if __name__ == "__main__":
    unittest.main()
