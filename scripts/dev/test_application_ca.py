"""Offline tests for explicit public application CA lookup."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.dev import certificates


class ApplicationCATests(unittest.TestCase):
    def test_only_public_ca_path_is_used(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ca = root / "rootCA.pem"
            ca.write_text("public certificate")
            with patch.object(certificates, "get_ca_root", return_value=root):
                self.assertEqual(certificates.curl_ca_args(), ["--cacert", str(ca)])

    def test_unavailable_ca_never_falls_back(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for candidate in (None, Path("relative"), root):
                with patch.object(certificates, "get_ca_root", return_value=candidate):
                    with self.assertRaises(certificates.LocalCAError):
                        certificates.curl_ca_args()
            ca = root / "rootCA.pem"
            ca.touch()
            with patch.object(certificates, "get_ca_root", return_value=root):
                with self.assertRaises(certificates.LocalCAError):
                    certificates.curl_ca_args()
                ca.unlink()
                ca.symlink_to(root / "other.pem")
                (root / "other.pem").write_text("other certificate")
                with self.assertRaises(certificates.LocalCAError):
                    certificates.curl_ca_args()


if __name__ == "__main__":
    unittest.main()
