"""Offline regression tests for trusted bootstrap dispatch."""

import io
import subprocess
import sys
import unittest

from contextlib import redirect_stderr
from unittest.mock import Mock, call, patch

from scripts.dev import bootstrap_unified as bootstrap


class UnifiedDispatchTests(unittest.TestCase):

    def test_all_direct_checks_use_allowlisted_modules(self):
        for flag, filename in bootstrap.DIRECT_READ_ONLY_CHECKS.items():
            with self.subTest(flag=flag):
                result = subprocess.CompletedProcess(
                    args=[], returncode=0
                )
                with patch.object(
                    bootstrap.subprocess, "run", return_value=result
                ) as run:
                    self.assertTrue(bootstrap.command(flag, quiet=True))

                argv = run.call_args.args[0]
                kwargs = run.call_args.kwargs

                self.assertEqual(argv, [
                    sys.executable,
                    str(bootstrap.ROOT / "scripts/dev" / filename),
                    "check",
                ])
                self.assertEqual(kwargs["cwd"], bootstrap.ROOT)
                self.assertIs(kwargs["stdout"], subprocess.DEVNULL)
                self.assertIs(kwargs["stderr"], subprocess.DEVNULL)
                self.assertIn("FT_DEV_TLS_DIR", kwargs["env"])

    def test_prepare_still_uses_trusted_shell(self):
        with patch.object(
            bootstrap.subprocess, "run",
            return_value=subprocess.CompletedProcess([], 0),
        ) as run:
            self.assertTrue(
                bootstrap.command("--prepare-prometheus")
            )

        self.assertEqual(
            run.call_args.args[0],
            [str(bootstrap.BOOTSTRAP), "--prepare-prometheus"],
        )

    def test_host_check_still_uses_trusted_shell(self):
        with patch.object(
            bootstrap.subprocess, "run",
            return_value=subprocess.CompletedProcess([], 0),
        ) as run:
            self.assertTrue(bootstrap.command("--check-host"))

        self.assertEqual(
            run.call_args.args[0],
            [str(bootstrap.BOOTSTRAP), "--check-host"],
        )

    def test_unknown_operation_rejected(self):
        with patch.object(bootstrap.subprocess, "run") as run:
            with self.assertRaises(bootstrap.UnifiedStartupError):
                bootstrap.command("--untrusted-operation")
            run.assert_not_called()

    def test_preflight_uses_trusted_shell(self):
        with patch.object(
            bootstrap.subprocess, "run",
            return_value=subprocess.CompletedProcess([], 0),
        ) as run:
            bootstrap.preflight_once()

        self.assertEqual(
            run.call_args.args[0],
            [str(bootstrap.BOOTSTRAP), "--orchestrator-preflight"],
        )
        self.assertEqual(
            run.call_args.kwargs["timeout"], 60
        )

    def test_preflight_failure_rejected(self):
        with patch.object(
            bootstrap.subprocess, "run",
            return_value=subprocess.CompletedProcess([], 1),
        ):
            with self.assertRaises(bootstrap.UnifiedStartupError):
                bootstrap.preflight_once()

    def test_preflight_timeout_rejected(self):
        with patch.object(
            bootstrap.subprocess, "run",
            side_effect=subprocess.TimeoutExpired("bootstrap", 60),
        ):
            with self.assertRaises(bootstrap.UnifiedStartupError):
                bootstrap.preflight_once()

    def test_preflight_runs_before_health_check(self):
        tracker = Mock()

        with (
            patch.object(bootstrap.os, "geteuid", return_value=1000),
            patch.object(bootstrap, "preflight_once") as preflight,
            patch.object(bootstrap, "check") as health,
        ):
            tracker.attach_mock(preflight, "preflight")
            tracker.attach_mock(health, "health")

            self.assertEqual(bootstrap.main(["check"]), 0)

        self.assertEqual(tracker.mock_calls, [
            call.preflight(),
            call.health(),
        ])

    def test_failed_preflight_blocks_health_check(self):
        with (
            patch.object(bootstrap.os, "geteuid", return_value=1000),
            patch.object(
                bootstrap,
                "preflight_once",
                side_effect=bootstrap.UnifiedStartupError(
                    "Preflight failed"
                ),
            ),
            patch.object(bootstrap, "check") as health,
            redirect_stderr(io.StringIO()),
        ):
            self.assertEqual(bootstrap.main(["check"]), 1)
            health.assert_not_called()


if __name__ == "__main__":
    unittest.main()
