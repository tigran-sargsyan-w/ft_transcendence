
"""Tests for local TLS certificate diagnostics."""

import io
import shutil
import subprocess
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


@unittest.skipUnless(
    shutil.which("openssl"),
    "OpenSSL is required",
)
class CertificateCryptographyTests(unittest.TestCase):

    def test_valid_certificate_is_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            ca_root = root / "ca"
            ca_root.mkdir()

            cert_file = root / "cert.pem"
            key_file = root / "key.pem"
            csr_file = root / "request.csr"
            extensions_file = root / "extensions.cnf"

            def openssl(*args):
                subprocess.run(
                    ["openssl", *map(str, args)],
                    check=True,
                    capture_output=True,
                    text=True,
                )

            # 1. Create an isolated, temporary CA.
            openssl(
                "req", "-x509",
                "-newkey", "rsa:2048",
                "-nodes",
                "-days", "365",
                "-keyout", ca_root / "rootCA-key.pem",
                "-out", ca_root / "rootCA.pem",
                "-subj", "/CN=ft-transcendence-test-CA",
                "-addext",
                "basicConstraints=critical,CA:TRUE",
                "-addext",
                "keyUsage=critical,keyCertSign,cRLSign",
            )

            # 2. Generate TLS private key and CSR.
            openssl(
                "req", "-new",
                "-newkey", "rsa:2048",
                "-nodes",
                "-keyout", key_file,
                "-out", csr_file,
                "-subj", "/CN=transcendence.test",
            )

            # 3. Configure TLS extensions.
            extensions_file.write_text(
                "basicConstraints=CA:FALSE\n"
                "keyUsage=digitalSignature,keyEncipherment\n"
                "extendedKeyUsage=serverAuth\n"
                "subjectAltName="
                "DNS:transcendence.test,"
                "DNS:localhost,"
                "IP:127.0.0.1\n",
                encoding="utf-8",
            )

            # 4. Sign the TLS certificate.
            openssl(
                "x509", "-req",
                "-in", csr_file,
                "-CA", ca_root / "rootCA.pem",
                "-CAkey", ca_root / "rootCA-key.pem",
                "-CAcreateserial",
                "-out", cert_file,
                "-days", "90",
                "-sha256",
                "-extfile", extensions_file,
            )

            # 5. Generate an unrelated private key.
            wrong_key = root / "wrong-key.pem"

            openssl(
                "genpkey",
                "-algorithm", "RSA",
                "-out", wrong_key,
                "-pkeyopt", "rsa_keygen_bits:2048",
            )

            # 6. Test real certificate validation.
            with (
                patch.object(
                    certificates, "CERT_FILE", cert_file
                ),
                patch.object(
                    certificates, "KEY_FILE", key_file
                ),
            ):
                # Case A: Valid certificate.
                valid, message = (
                    certificates.validate_certificate(
                        ca_root,
                        "transcendence.test",
                    )
                )

                self.assertTrue(valid, message)

                # Case B: Wrong hostname.
                valid, message = (
                    certificates.validate_certificate(
                        ca_root,
                        "wrong.test",
                    )
                )

                self.assertFalse(valid)
                self.assertEqual(
                    message,
                    "Certificate chain or hostname verification failed",
                )

                # Case C: Wrong private key.
                with patch.object(
                    certificates, "KEY_FILE", wrong_key
                ):
                    valid, message = (
                        certificates.validate_certificate(
                            ca_root,
                            "transcendence.test",
                        )
                    )

                    self.assertFalse(valid)
                    self.assertEqual(
                        message,
                        "TLS certificate and private key do not match",
                    )


if __name__ == "__main__":
    unittest.main()
