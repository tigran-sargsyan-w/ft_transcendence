
"""Tests for unified CA trust management."""

import io
import tempfile
import unittest

from contextlib import ExitStack, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from scripts.dev import ca_trust


class UnifiedCATrustTests(unittest.TestCase):

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

        self.platform = mock(
            ca_trust.platform,
            "system",
            return_value="Linux",
        )

        self.is_wsl = mock(
            ca_trust.domains,
            "is_wsl",
            return_value=True,
        )

        self.check_linux = mock(
            ca_trust,
            "check_linux",
            return_value=True,
        )

        self.check_windows = mock(
            ca_trust,
            "check_windows",
            return_value=True,
        )

        self.setup_linux = mock(
            ca_trust,
            "setup_linux",
            return_value=0,
        )

        self.setup_windows = mock(
            ca_trust,
            "setup_windows",
            return_value=0,
        )

        self.inspect = mock(
            ca_trust,
            "inspect",
            return_value=0,
        )

    def run_setup(self):
        with redirect_stdout(io.StringIO()):
            return ca_trust.setup()

    def test_wsl_already_trusted(self):
        result = self.run_setup()

        self.assertEqual(result, 0)

        self.check_linux.assert_called_once_with(
            self.ca_file
        )
        self.check_windows.assert_called_once_with(
            self.thumbprint
        )

        self.setup_linux.assert_not_called()
        self.setup_windows.assert_not_called()

        self.inspect.assert_called_once_with("check")

    def test_wsl_installs_missing_linux_trust(self):
        self.check_linux.return_value = False

        result = self.run_setup()

        self.assertEqual(result, 0)
        self.setup_linux.assert_called_once()

        self.setup_windows.assert_not_called()
        self.inspect.assert_called_once_with("check")

    def test_wsl_installs_missing_windows_trust(self):
        self.check_windows.return_value = False

        result = self.run_setup()

        self.assertEqual(result, 0)

        self.setup_linux.assert_not_called()
        self.setup_windows.assert_called_once_with(
            self.ca_file,
            self.thumbprint,
        )

        self.inspect.assert_called_once_with("check")

    def test_native_linux_skips_windows(self):
        self.is_wsl.return_value = False

        result = self.run_setup()

        self.assertEqual(result, 0)

        self.check_linux.assert_called_once()
        self.check_windows.assert_not_called()
        self.setup_windows.assert_not_called()

    def test_native_windows_skips_linux(self):
        self.platform.return_value = "Windows"
        self.check_windows.return_value = False

        result = self.run_setup()

        self.assertEqual(result, 0)

        self.check_linux.assert_not_called()
        self.setup_linux.assert_not_called()

        self.setup_windows.assert_called_once_with(
            self.ca_file,
            self.thumbprint,
        )

    def test_windows_installation_declined(self):
        self.check_windows.return_value = False
        self.setup_windows.return_value = 1

        result = self.run_setup()

        self.assertEqual(result, 1)

        self.setup_windows.assert_called_once()
        self.inspect.assert_not_called()

    def test_linux_installation_failure(self):
        self.check_linux.return_value = False
        self.setup_linux.return_value = 2

        result = self.run_setup()

        self.assertEqual(result, 2)

        self.setup_linux.assert_called_once()
        self.check_windows.assert_not_called()
        self.inspect.assert_not_called()

    def test_windows_check_failure(self):
        self.check_windows.return_value = None

        result = self.run_setup()

        self.assertEqual(result, 2)

        self.setup_windows.assert_not_called()
        self.inspect.assert_not_called()


if __name__ == "__main__":
    unittest.main()
