
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
        for path in [
            self.ca_root / "rootCA.pem",
            self.ca_root / "rootCA-key.pem",
            self.cert_file,
            self.key_file,
        ]:
            self.create_file(path)

        self.assertEqual(self.run_check(), 1)


@unittest.skipUnless(
    shutil.which("openssl"),
    "OpenSSL is required",
)
class CertificateCryptographyTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)

        cls.root = Path(cls.temp.name)
        cls.ca_root = cls.root / "ca"
        cls.ca_root.mkdir()

        cls.key_file = cls.root / "key.pem"
        cls.wrong_key = cls.root / "wrong-key.pem"
        cls.csr_file = cls.root / "request.csr"

        # Generate a temporary CA.
        cls.openssl(
            "req", "-x509",
            "-newkey", "rsa:2048",
            "-nodes",
            "-days", "365",
            "-keyout", cls.ca_root / "rootCA-key.pem",
            "-out", cls.ca_root / "rootCA.pem",
            "-subj", "/CN=ft-transcendence-test-CA",
            "-addext",
            "basicConstraints=critical,CA:TRUE",
            "-addext",
            "keyUsage=critical,keyCertSign,cRLSign",
        )

        # Generate the server's private key and CSR.
        cls.openssl(
            "req", "-new",
            "-newkey", "rsa:2048",
            "-nodes",
            "-keyout", cls.key_file,
            "-out", cls.csr_file,
            "-subj", "/CN=transcendence.test",
        )

        # Generate an unrelated private key.
        cls.openssl(
            "genpkey",
            "-algorithm", "RSA",
            "-out", cls.wrong_key,
            "-pkeyopt", "rsa_keygen_bits:2048",
        )

    @staticmethod
    def openssl(*args):
        subprocess.run(
            ["openssl", *map(str, args)],
            check=True,
            capture_output=True,
            text=True,
        )

    def issue_certificate(
        self,
        name,
        san,
        days=90,
    ):
        """Issue a temporary certificate with chosen extensions."""

        cert_file = self.root / f"{name}.pem"
        extensions_file = self.root / f"{name}.cnf"

        extensions_file.write_text(
            "basicConstraints=CA:FALSE\n"
            "keyUsage=digitalSignature,keyEncipherment\n"
            "extendedKeyUsage=serverAuth\n"
            f"subjectAltName={san}\n",
            encoding="utf-8",
        )

        self.openssl(
            "x509", "-req",
            "-in", self.csr_file,
            "-CA", self.ca_root / "rootCA.pem",
            "-CAkey", self.ca_root / "rootCA-key.pem",
            "-CAcreateserial",
            "-out", cert_file,
            "-days", str(days),
            "-sha256",
            "-extfile", extensions_file,
        )

        return cert_file

    def validate(
        self,
        cert_file,
        key_file=None,
        domain="transcendence.test",
    ):
        if key_file is None:
            key_file = self.key_file

        with (
            patch.object(
                certificates, "CERT_FILE", cert_file
            ),
            patch.object(
                certificates, "KEY_FILE", key_file
            ),
        ):
            return certificates.validate_certificate(
                self.ca_root,
                domain,
            )

    def test_valid_certificate_is_accepted(self):
        cert = self.issue_certificate(
            "valid",
            "DNS:transcendence.test,"
            "DNS:localhost,"
            "IP:127.0.0.1",
        )

        valid, message = self.validate(cert)

        self.assertTrue(valid, message)

    def test_wrong_hostname_is_rejected(self):
        cert = self.issue_certificate(
            "wrong-hostname",
            "DNS:transcendence.test,"
            "DNS:localhost,"
            "IP:127.0.0.1",
        )

        valid, message = self.validate(
            cert,
            domain="wrong.test",
        )

        self.assertFalse(valid)
        self.assertEqual(
            message,
            "Certificate chain or hostname verification failed",
        )

    def test_wrong_private_key_is_rejected(self):
        cert = self.issue_certificate(
            "mismatched-key-cert",
            "DNS:transcendence.test,"
            "DNS:localhost,"
            "IP:127.0.0.1",
        )

        valid, message = self.validate(
            cert,
            key_file=self.wrong_key,
        )

        self.assertFalse(valid)
        self.assertEqual(
            message,
            "TLS certificate and private key do not match",
        )

    def test_missing_dns_san_is_rejected(self):
        # localhost is deliberately missing.
        cert = self.issue_certificate(
            "missing-dns",
            "DNS:transcendence.test,"
            "IP:127.0.0.1",
        )

        valid, message = self.validate(cert)

        self.assertFalse(valid)
        self.assertEqual(
            message,
            "Missing required DNS SAN entries",
        )

    def test_missing_ip_san_is_rejected(self):
        # 127.0.0.1 is deliberately missing.
        cert = self.issue_certificate(
            "missing-ip",
            "DNS:transcendence.test,"
            "DNS:localhost",
        )

        valid, message = self.validate(cert)

        self.assertFalse(valid)
        self.assertEqual(
            message,
            "Missing required IP SAN entries",
        )

    def test_expiring_certificate_is_rejected(self):
        # A certificate valid for only 1 day.
        cert = self.issue_certificate(
            "expiring",
            "DNS:transcendence.test,"
            "DNS:localhost,"
            "IP:127.0.0.1",
            days=1,
        )

        valid, message = self.validate(cert)

        self.assertFalse(valid)
        self.assertEqual(
            message,
            "Certificate invalid or expiring within 30 days",
        )


if __name__ == "__main__":
    unittest.main()
