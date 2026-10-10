
"""Tests for Linux CA trust uninstall."""

import io
import tempfile
import unittest

from contextlib import ExitStack, redirect_stdout
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

from scripts.dev import ca_trust


class LinuxCATrustUninstallTests(unittest.TestCase):

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

        self.ca_root = Path(self.temp.name)

        self.ca_file = self.ca_root / "rootCA.pem"
        self.ca_key = self.ca_root / "rootCA-key.pem"

        self.ca_file.write_text("test CA", encoding="utf-8")
        self.ca_key.write_text("test CA key", encoding="utf-8")

        self.stack = ExitStack()
        self.addCleanup(self.stack.close)

        self.stack.enter_context(
            patch.object(
                ca_trust.platform,
                "system",
                return_value="Linux",
            )
        )

        self.stack.enter_context(
            patch.object(
                ca_trust.certificates,
                "get_ca_root",
                return_value=self.ca_root,
            )
        )

        self.stack.enter_context(
            patch.object(
                ca_trust,
                "get_thumbprint",
                return_value="A" * 40,
            )
        )

        self.check_linux = self.stack.enter_context(
            patch.object(
                ca_trust,
                "check_linux",
                return_value=True,
            )
        )

        self.run_command = self.stack.enter_context(
            patch.object(
                ca_trust.subprocess,
                "run",
                return_value=CompletedProcess(
                    args=["mkcert", "-uninstall"],
                    returncode=0,
                ),
            )
        )

    def uninstall(self, answer="y"):
        with (
            patch("builtins.input", return_value=answer),
            redirect_stdout(io.StringIO()),
        ):
            return ca_trust.uninstall_linux()

    def test_uninstall_declined(self):
        result = self.uninstall("n")

        self.assertEqual(result, 1)
        self.run_command.assert_not_called()

        self.assertTrue(self.ca_file.exists())
        self.assertTrue(self.ca_key.exists())

    def test_uninstall_success(self):
        # Trusted before uninstall, untrusted afterwards.
        self.check_linux.side_effect = [True, False]

        result = self.uninstall("y")

        self.assertEqual(result, 0)

        self.run_command.assert_called_once_with(
            ["mkcert", "-uninstall"],
            check=False,
        )

        # Root CA files must survive.
        self.assertTrue(self.ca_file.exists())
        self.assertTrue(self.ca_key.exists())

    def test_uninstall_command_failure(self):
        self.run_command.return_value = CompletedProcess(
            args=["mkcert", "-uninstall"],
            returncode=1,
        )

        result = self.uninstall("y")

        self.assertEqual(result, 2)
        self.run_command.assert_called_once()

    def test_uninstall_still_trusted(self):
        # mkcert succeeds, but OpenSSL still trusts the CA.
        self.check_linux.side_effect = [True, True]

        result = self.uninstall("y")

        self.assertEqual(result, 2)

    def test_uninstall_idempotency(self):
        # CA is already untrusted.
        self.check_linux.return_value = False

        result = self.uninstall()

        self.assertEqual(result, 0)
        self.run_command.assert_not_called()


if __name__ == "__main__":
    unittest.main()
