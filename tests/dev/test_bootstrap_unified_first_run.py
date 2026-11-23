"""Offline tests for first-run bootstrap retry optimization."""

import subprocess
import unittest
from unittest.mock import patch

from scripts.dev import bootstrap_unified as bootstrap


class FirstRunRetryTests(unittest.TestCase):

    def test_missing_container_skips_retries(self):
        with (
            patch.object(bootstrap, "command", return_value=False) as command,
            patch.object(
                bootstrap, "definitely_missing_container",
                return_value=True
            ),
            patch.object(bootstrap.time, "sleep") as sleep,
        ):
            self.assertFalse(
                bootstrap.checked("prometheus", first_run=True)
            )
            self.assertEqual(command.call_count, 1)
            sleep.assert_not_called()

    def test_existing_unhealthy_container_preserves_retries(self):
        with (
            patch.object(bootstrap, "command", return_value=False) as command,
            patch.object(
                bootstrap, "definitely_missing_container",
                return_value=False
            ),
            patch.object(bootstrap.time, "sleep") as sleep,
        ):
            self.assertFalse(
                bootstrap.checked("prometheus", first_run=True)
            )
            self.assertEqual(command.call_count, 3)
            self.assertEqual(sleep.call_count, 2)

    def test_routine_checks_preserve_retries(self):
        with (
            patch.object(bootstrap, "command", return_value=False) as command,
            patch.object(
                bootstrap, "definitely_missing_container"
            ) as missing,
            patch.object(bootstrap.time, "sleep") as sleep,
        ):
            self.assertFalse(bootstrap.checked("prometheus"))
            self.assertEqual(command.call_count, 3)
            self.assertEqual(sleep.call_count, 2)
            missing.assert_not_called()

    def test_success_never_checks_absence(self):
        with (
            patch.object(bootstrap, "command", return_value=True) as command,
            patch.object(
                bootstrap, "definitely_missing_container"
            ) as missing,
        ):
            self.assertTrue(
                bootstrap.checked("prometheus", first_run=True)
            )
            command.assert_called_once()
            missing.assert_not_called()

    def test_all_containers_missing(self):
        with patch.object(
            bootstrap.subprocess,
            "run",
            return_value=subprocess.CompletedProcess(
                args=["docker"], returncode=0, stdout=b""
            ),
        ):
            self.assertTrue(
                bootstrap.definitely_missing_container("support-services")
            )

    def test_partial_installation_preserves_retries(self):
        with patch.object(
            bootstrap.subprocess,
            "run",
            return_value=subprocess.CompletedProcess(
                args=["docker"],
                returncode=0,
                stdout=b"transcendence-app-dev-collector-1\n",
            ),
        ):
            self.assertFalse(
                bootstrap.definitely_missing_container("support-services")
            )

    def test_docker_failure_preserves_retries(self):
        with patch.object(
            bootstrap.subprocess,
            "run",
            return_value=subprocess.CompletedProcess(
                args=["docker"], returncode=1, stdout=b""
            ),
        ):
            self.assertFalse(
                bootstrap.definitely_missing_container("prometheus")
            )

    def test_docker_timeout_preserves_retries(self):
        with patch.object(
            bootstrap.subprocess,
            "run",
            side_effect=subprocess.TimeoutExpired("docker", 5),
        ):
            self.assertFalse(
                bootstrap.definitely_missing_container("prometheus")
            )

    def test_invalid_docker_output_preserves_retries(self):
        with patch.object(
            bootstrap.subprocess,
            "run",
            return_value=subprocess.CompletedProcess(
                args=["docker"], returncode=0, stdout=b"\xff"
            ),
        ):
            self.assertFalse(
                bootstrap.definitely_missing_container("prometheus")
            )

    def test_unlisted_stage_is_not_optimized(self):
        with patch.object(bootstrap.subprocess, "run") as run:
            self.assertFalse(
                bootstrap.definitely_missing_container("vault-secrets")
            )
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
