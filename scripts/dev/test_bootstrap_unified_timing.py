"""Offline P6 tests for optional, non-secret unified stage timings."""
import contextlib
import io
import os
import unittest
from unittest.mock import patch
import bootstrap_unified as unified


class StageTimingTests(unittest.TestCase):
    def test_default_has_no_extra_output(self):
        with patch.dict(os.environ, {"FT_DEV_STAGE_TIMING": ""}), \
             patch.object(unified, "command", return_value=True) as cmd, \
             contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertTrue(unified.checked("nginx"))
        self.assertEqual(output.getvalue(), "")
        cmd.assert_called_once_with("--check-nginx", quiet=True)

    def test_enabled_single_success_reports_only_safe_stage(self):
        with patch.dict(os.environ, {"FT_DEV_STAGE_TIMING": "1"}), \
             patch.object(unified, "command", return_value=True) as cmd, \
             patch.object(unified.time, "monotonic", side_effect=[10, 10.25]), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertTrue(unified.checked("resources"))
        self.assertEqual(output.getvalue(),
            "TIMING: stage=resources attempt=1/3 result=PASS elapsed=0.25s\n")
        cmd.assert_called_once_with("--check-resources", quiet=True)

    def test_retry_reports_first_failure_then_success(self):
        with patch.dict(os.environ, {"FT_DEV_STAGE_TIMING": "1"}), \
             patch.object(unified, "command", side_effect=[False, True]) as cmd, \
             patch.object(unified.time, "monotonic", side_effect=[1, 1.5, 2, 2.25]), \
             patch.object(unified.time, "sleep") as sleep, \
             contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertTrue(unified.checked("nginx"))
        self.assertEqual(cmd.call_count, 2)
        sleep.assert_called_once_with(unified.READ_ONLY_RETRY_DELAY)
        self.assertEqual(output.getvalue().splitlines(), [
            "TIMING: stage=nginx attempt=1/3 result=FAIL elapsed=0.50s",
            "TIMING: stage=nginx attempt=2/3 result=PASS elapsed=0.25s",
        ])

    def test_persistent_failure_remains_failure_and_never_prepares(self):
        with patch.dict(os.environ, {"FT_DEV_STAGE_TIMING": "1"}), \
             patch.object(unified, "command", return_value=False) as cmd, \
             patch.object(unified.time, "monotonic", side_effect=[0, 1, 2, 3, 4, 5]), \
             patch.object(unified.time, "sleep") as sleep, \
             contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertFalse(unified.checked("resources"))
        self.assertEqual(cmd.call_count, unified.READ_ONLY_ATTEMPTS)
        self.assertEqual(sleep.call_count, unified.READ_ONLY_ATTEMPTS - 1)
        self.assertEqual(output.getvalue().count("result=FAIL"), unified.READ_ONLY_ATTEMPTS)
        self.assertTrue(all(c.args[0] == "--check-resources" for c in cmd.call_args_list))

    def test_non_one_value_does_not_enable_trace(self):
        with patch.dict(os.environ, {"FT_DEV_STAGE_TIMING": "true"}), \
             patch.object(unified, "command", return_value=True), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertTrue(unified.checked("host"))
        self.assertEqual(output.getvalue(), "")

    def test_debug_does_not_change_health_stage_order(self):
        with patch.dict(os.environ, {"FT_DEV_STAGE_TIMING": "1"}), \
             patch.object(unified, "command", return_value=True) as cmd, \
             patch.object(unified.time, "monotonic", return_value=0), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            unified.health_report()
        self.assertEqual([x.args[0] for x in cmd.call_args_list],
                         ["--check-host", "--check-resources", "--check-nginx"])
        self.assertEqual(output.getvalue().count("TIMING:"), 3)
        self.assertIn("RESULT: DEVELOPMENT HEALTHY (read-only)", output.getvalue())


if __name__ == "__main__":
    unittest.main()
