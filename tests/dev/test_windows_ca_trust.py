
"""Integration tests for Windows CA trust adapter under WSL."""

import platform
import shutil
import subprocess
import tempfile
import unittest

from pathlib import Path

from scripts.dev import domains


REQUIRES_WSL = (
    platform.system() == "Linux"
    and domains.is_wsl()
    and shutil.which("powershell.exe") is not None
    and shutil.which("wslpath") is not None
    and shutil.which("openssl") is not None
)


@unittest.skipUnless(
    REQUIRES_WSL,
    "Windows CA trust integration tests require WSL",
)
class WindowsCATrustTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)

        cls.root = Path(cls.temp.name)
        cls.ca_file = cls.root / "rootCA.pem"
        cls.key_file = cls.root / "rootCA-key.pem"

        cls.script = (
            Path(__file__).resolve().parents[2]
            / "scripts"
            / "dev"
            / "windows_ca_trust.ps1"
        )

        # Create an isolated CA without installing trust.
        subprocess.run(
            [
                "openssl", "req",
                "-x509",
                "-newkey", "rsa:2048",
                "-nodes",
                "-sha256",
                "-days", "3",
                "-keyout", str(cls.key_file),
                "-out", str(cls.ca_file),
                "-subj", "/CN=ft-transcendence-untrusted-test-CA",
                "-addext", "basicConstraints=critical,CA:TRUE",
                "-addext", "keyUsage=critical,keyCertSign,cRLSign",
            ],
            check=True,
            capture_output=True,
            text=True,
        )

        fingerprint = subprocess.run(
            [
                "openssl", "x509",
                "-in", str(cls.ca_file),
                "-noout",
                "-fingerprint",
                "-sha1",
            ],
            check=True,
            capture_output=True,
            text=True,
        )

        cls.thumbprint = (
            fingerprint.stdout.split("=", 1)[1]
            .replace(":", "")
            .strip()
        )

        def windows_path(path):
            result = subprocess.run(
                ["wslpath", "-w", str(path)],
                capture_output=True,
                text=True,
                check=True,
            )
            return result.stdout.strip()

        cls.windows_ca = windows_path(cls.ca_file)
        cls.windows_script = windows_path(cls.script)

    def run_adapter(self, action, thumbprint=None):
        return subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy", "Bypass",
                "-File", self.windows_script,
                "-Action", action,
                "-CaPath", self.windows_ca,
                "-ExpectedThumbprint",
                thumbprint or self.thumbprint,
            ],
            capture_output=True,
            text=True,
            check=False,
        )

    def test_untrusted_ca_is_detected(self):
        result = self.run_adapter("check")

        self.assertEqual(
            result.returncode,
            1,
            result.stdout + result.stderr,
        )

        self.assertIn(
            "[MISSING] CurrentUser Root",
            result.stdout,
        )
        self.assertIn(
            "[MISSING] LocalMachine Root",
            result.stdout,
        )

        self.assertNotIn(
            "already configured",
            result.stdout,
        )

    def test_plan_does_not_install_ca(self):
        result = self.run_adapter("plan")

        self.assertEqual(result.returncode, 0)
        self.assertIn("[PLAN]", result.stdout)

        # The CA must remain untrusted.
        check = self.run_adapter("check")
        self.assertEqual(check.returncode, 1)

    def test_setup_without_apply_is_blocked(self):
        result = self.run_adapter("setup")

        self.assertEqual(result.returncode, 1)
        self.assertIn(
            "Use -Action setup -Apply to confirm",
            result.stdout,
        )

        # No trust-store modification.
        check = self.run_adapter("check")
        self.assertEqual(check.returncode, 1)

    def test_incorrect_thumbprint_is_rejected(self):
        result = self.run_adapter(
            "check",
            thumbprint="B" * 40,
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn(
            "CA thumbprint mismatch",
            result.stderr,
        )


    def test_uninstall_untrusted_ca_is_idempotent(self):
        # The temporary CA was never installed.
        result = self.run_adapter("uninstall")

        self.assertEqual(
            result.returncode,
            0,
            result.stdout + result.stderr,
        )

        self.assertIn(
            "[OK] CA is not in CurrentUser Root",
            result.stdout,
        )

        # It must remain untrusted.
        check = self.run_adapter("check")
        self.assertEqual(check.returncode, 1)

    def test_uninstall_wrong_thumbprint_is_rejected(self):
        result = self.run_adapter(
            "uninstall",
            thumbprint="B" * 40,
        )

        self.assertEqual(result.returncode, 2)

        self.assertIn(
            "CA thumbprint mismatch",
            result.stderr,
        )


if __name__ == "__main__":
    unittest.main()
