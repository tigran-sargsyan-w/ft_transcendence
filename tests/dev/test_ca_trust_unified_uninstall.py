
"""Tests for unified CA trust uninstall coordinator."""

import io
import tempfile
import unittest

from contextlib import ExitStack, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from scripts.dev import ca_trust


class UnifiedCATrustUninstallTests(unittest.TestCase):

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

        self.ca_root = Path(self.temp.name)
        self.ca_file = self.ca_root / "rootCA.pem"
        self.ca_file.write_text("test", encoding="utf-8")

        self.thumbprint = "A" * 40

        self.stack = ExitStack()
        self.addCleanup(self.stack.close)

        def mock(target, name, **kwargs):
            return self.stack.enter_context(
                patch.object(target, name, **kwargs)
            )

        self.platform = mock(
            ca_trust.platform,
            "system",
            return_value="Linux",
        )

        self.ca_root_lookup = mock(
            ca_trust.certificates,
            "get_ca_root",
            return_value=self.ca_root,
        )

        self.get_thumbprint = mock(
            ca_trust,
            "get_thumbprint",
            return_value=self.thumbprint,
        )

        self.is_wsl = mock(
            ca_trust.domains,
            "is_wsl",
            return_value=True,
        )

        self.uninstall_linux = mock(
            ca_trust,
            "uninstall_linux",
            return_value=0,
        )

        self.uninstall_windows = mock(
            ca_trust,
            "uninstall_windows",
            return_value=0,
        )

    def run_uninstall(self):
        with redirect_stdout(io.StringIO()):
            return ca_trust.uninstall()

    def test_wsl_uninstalls_both(self):
        result = self.run_uninstall()

        self.assertEqual(result, 0)
        self.uninstall_linux.assert_called_once_with()
        self.uninstall_windows.assert_called_once_with(
            self.ca_file,
            self.thumbprint,
        )

    def test_native_linux_skips_windows(self):
        self.is_wsl.return_value = False

        result = self.run_uninstall()

        self.assertEqual(result, 0)
        self.uninstall_linux.assert_called_once()
        self.uninstall_windows.assert_not_called()

    def test_native_windows_skips_linux(self):
        self.platform.return_value = "Windows"

        result = self.run_uninstall()

        self.assertEqual(result, 0)
        self.uninstall_linux.assert_not_called()
        self.uninstall_windows.assert_called_once_with(
            self.ca_file,
            self.thumbprint,
        )

    def test_linux_decline_stops_uninstall(self):
        self.uninstall_linux.return_value = 1

        result = self.run_uninstall()

        self.assertEqual(result, 1)
        self.uninstall_windows.assert_not_called()

    def test_linux_failure_stops_uninstall(self):
        self.uninstall_linux.return_value = 2

        result = self.run_uninstall()

        self.assertEqual(result, 2)
        self.uninstall_windows.assert_not_called()

    def test_windows_failure_is_reported(self):
        self.uninstall_windows.return_value = 2

        result = self.run_uninstall()

        self.assertEqual(result, 2)
        self.uninstall_linux.assert_called_once()
        self.uninstall_windows.assert_called_once()

    def test_missing_ca_stops_uninstall(self):
        self.ca_file.unlink()

        result = self.run_uninstall()

        self.assertEqual(result, 2)
        self.uninstall_linux.assert_not_called()
        self.uninstall_windows.assert_not_called()


if __name__ == "__main__":
    unittest.main()
