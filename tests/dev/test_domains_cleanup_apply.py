
"""Tests for unified domain cleanup --apply."""

import sys
import unittest
from subprocess import CompletedProcess
from unittest.mock import patch

from scripts.dev import domains


DOMAIN = "transcendence.test"


class UnifiedDomainCleanupTests(unittest.TestCase):

    def test_linux_cleanup_apply_uses_real_cleanup(self):
        with patch.object(
            domains.subprocess,
            "run",
            return_value=CompletedProcess([], 0),
        ) as run:
            result = domains.run_linux("cleanup-apply", DOMAIN)

        self.assertEqual(result, 0)
        command = run.call_args.args[0]

        self.assertEqual(
            command,
            [
                sys.executable,
                "-m",
                "scripts.dev.linux_hosts",
                "cleanup",
                "--domain",
                DOMAIN,
            ],
        )

    def test_linux_cleanup_preview_is_preserved(self):
        with patch.object(
            domains.subprocess,
            "run",
            return_value=CompletedProcess([], 1),
        ) as run:
            result = domains.run_linux("cleanup", DOMAIN)

        self.assertEqual(result, 1)
        command = run.call_args.args[0]

        self.assertEqual(command[3], "cleanup-plan")

    def test_windows_cleanup_apply_uses_apply_flag(self):
        with (
            patch.object(domains, "is_wsl", return_value=False),
            patch.object(
                domains.subprocess,
                "run",
                return_value=CompletedProcess([], 0),
            ) as run,
        ):
            result = domains.run_windows(
                "cleanup-apply", DOMAIN
            )

        self.assertEqual(result, 0)
        command = run.call_args.args[0]

        self.assertEqual(
            command[command.index("-Action") + 1],
            "cleanup",
        )
        self.assertIn("-Apply", command)

    def test_windows_cleanup_preview_has_no_apply(self):
        with (
            patch.object(domains, "is_wsl", return_value=False),
            patch.object(
                domains.subprocess,
                "run",
                return_value=CompletedProcess([], 1),
            ) as run,
        ):
            result = domains.run_windows("cleanup", DOMAIN)

        self.assertEqual(result, 1)
        command = run.call_args.args[0]

        self.assertEqual(
            command[command.index("-Action") + 1],
            "cleanup",
        )
        self.assertNotIn("-Apply", command)

    def test_wsl_cleanup_apply_runs_both_adapters(self):
        with (
            patch.object(
                domains.platform, "system",
                return_value="Linux",
            ),
            patch.object(domains, "is_wsl", return_value=True),
            patch.object(
                domains, "run_linux", return_value=0
            ) as linux,
            patch.object(
                domains, "run_windows", return_value=0
            ) as windows,
        ):
            result = domains.manage("cleanup-apply", DOMAIN)

        self.assertEqual(result, 0)
        linux.assert_called_once_with("cleanup-apply", DOMAIN)
        windows.assert_called_once_with("cleanup-apply", DOMAIN)

    def test_wsl_cleanup_apply_stops_after_linux_failure(self):
        with (
            patch.object(
                domains.platform, "system",
                return_value="Linux",
            ),
            patch.object(domains, "is_wsl", return_value=True),
            patch.object(
                domains, "run_linux", return_value=1
            ),
            patch.object(domains, "run_windows") as windows,
        ):
            result = domains.manage("cleanup-apply", DOMAIN)

        self.assertEqual(result, 1)
        windows.assert_not_called()

    def test_cli_apply_maps_to_cleanup_apply(self):
        with (
            patch.object(
                domains.sys,
                "argv",
                ["domains", "cleanup", "--apply"],
            ),
            patch.object(
                domains,
                "manage",
                return_value=0,
            ) as manage,
        ):
            result = domains.main()

        self.assertEqual(result, 0)
        manage.assert_called_once_with(
            "cleanup-apply",
            domains.DEFAULT_DOMAIN,
        )


if __name__ == "__main__":
    unittest.main()
