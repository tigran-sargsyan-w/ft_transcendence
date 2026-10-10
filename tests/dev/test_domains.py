
import unittest
from unittest.mock import patch

from scripts.dev import domains


DOMAIN = "transcendence.test"


class DomainManagerTests(unittest.TestCase):

    def test_native_linux_uses_only_linux(self):
        with (
            patch.object(
                domains.platform, "system",
                return_value="Linux"
            ),
            patch.object(domains, "is_wsl", return_value=False),
            patch.object(
                domains, "run_linux", return_value=0
            ) as linux,
            patch.object(domains, "run_windows") as windows,
        ):
            self.assertEqual(domains.manage("setup", DOMAIN), 0)
            linux.assert_called_once_with("setup", DOMAIN)
            windows.assert_not_called()

    def test_wsl_uses_both_adapters(self):
        with (
            patch.object(
                domains.platform, "system",
                return_value="Linux"
            ),
            patch.object(domains, "is_wsl", return_value=True),
            patch.object(
                domains, "run_linux", return_value=0
            ) as linux,
            patch.object(
                domains, "run_windows", return_value=0
            ) as windows,
        ):
            self.assertEqual(domains.manage("setup", DOMAIN), 0)
            linux.assert_called_once_with("setup", DOMAIN)
            windows.assert_called_once_with("setup", DOMAIN)

    def test_windows_uses_only_windows(self):
        with (
            patch.object(
                domains.platform, "system",
                return_value="Windows"
            ),
            patch.object(domains, "is_wsl", return_value=False),
            patch.object(domains, "run_linux") as linux,
            patch.object(
                domains, "run_windows", return_value=0
            ) as windows,
        ):
            self.assertEqual(domains.manage("check", DOMAIN), 0)
            windows.assert_called_once_with("check", DOMAIN)
            linux.assert_not_called()

    def test_check_reports_missing_mapping(self):
        with (
            patch.object(
                domains.platform, "system",
                return_value="Linux"
            ),
            patch.object(domains, "is_wsl", return_value=True),
            patch.object(domains, "run_linux", return_value=0),
            patch.object(domains, "run_windows", return_value=1),
        ):
            self.assertEqual(domains.manage("check", DOMAIN), 1)

    def test_setup_stops_after_failure(self):
        with (
            patch.object(
                domains.platform, "system",
                return_value="Linux"
            ),
            patch.object(domains, "is_wsl", return_value=True),
            patch.object(domains, "run_linux", return_value=2),
            patch.object(domains, "run_windows") as windows,
        ):
            self.assertEqual(domains.manage("setup", DOMAIN), 2)
            windows.assert_not_called()

    def test_localhost_needs_no_adapter(self):
        with (
            patch.object(domains, "run_linux") as linux,
            patch.object(domains, "run_windows") as windows,
        ):
            self.assertEqual(
                domains.manage("setup", "localhost"), 0
            )
            linux.assert_not_called()
            windows.assert_not_called()

    def test_invalid_domain_is_rejected(self):
        with self.assertRaises(ValueError):
            domains.manage("setup", "invalid..test")

    def test_wsl_cleanup_previews_both_adapters(self):
        with (
            patch.object(
                domains.platform, "system",
                return_value="Linux",
            ),
            patch.object(domains, "is_wsl", return_value=True),
            patch.object(
                domains, "run_linux", return_value=1
            ) as linux,
            patch.object(
                domains, "run_windows", return_value=1
            ) as windows,
        ):
            result = domains.manage("cleanup", DOMAIN)

            self.assertEqual(result, 1)
            linux.assert_called_once_with("cleanup", DOMAIN)
            windows.assert_called_once_with("cleanup", DOMAIN)

    def test_native_linux_cleanup(self):
        with (
            patch.object(
                domains.platform, "system",
                return_value="Linux",
            ),
            patch.object(domains, "is_wsl", return_value=False),
            patch.object(
                domains, "run_linux", return_value=1
            ) as linux,
            patch.object(domains, "run_windows") as windows,
        ):
            result = domains.manage("cleanup", DOMAIN)

            self.assertEqual(result, 1)
            linux.assert_called_once_with("cleanup", DOMAIN)
            windows.assert_not_called()

    def test_cleanup_error_takes_priority(self):
        with (
            patch.object(
                domains.platform, "system",
                return_value="Linux",
            ),
            patch.object(domains, "is_wsl", return_value=True),
            patch.object(domains, "run_linux", return_value=2),
            patch.object(
                domains, "run_windows", return_value=1
            ) as windows,
        ):
            result = domains.manage("cleanup", DOMAIN)

            self.assertEqual(result, 2)
            windows.assert_called_once()



if __name__ == "__main__":
    unittest.main()
