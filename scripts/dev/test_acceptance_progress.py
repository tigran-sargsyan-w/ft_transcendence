"""Presentation contracts for existing guarded startup acceptance."""
from contextlib import redirect_stdout, redirect_stderr
import io
import os
import unittest
from unittest.mock import patch

import verify_startup_idempotency as acceptance
from test_verify_startup_idempotency import fixture


class AcceptanceProgressTests(unittest.TestCase):
    def execute(self, args, snapshots, *, trusted_error=None):
        order = []

        def trusted(flag):
            order.append(flag)
            if flag == trusted_error:
                raise acceptance.AcceptanceError("Expected trusted failure")

        items = iter(snapshots)

        def snapshot():
            order.append("snapshot")
            return next(items)

        with patch.object(acceptance.os, "geteuid", return_value=1000), \
                patch.object(acceptance, "trusted", side_effect=trusted), \
                patch.object(acceptance, "trusted_initialize",
                             side_effect=lambda: order.append("--initialize")), \
                patch.object(acceptance, "snapshot", side_effect=snapshot), \
                patch.dict(os.environ, {"CI": "1", "FT_DEV_BOOTSTRAP_VERBOSE": "1"}), \
                redirect_stdout(io.StringIO()) as out, redirect_stderr(io.StringIO()) as err:
            code = acceptance.main(args, render=True)
        return code, out.getvalue(), err.getvalue(), order

    def test_startup_order_and_actual_comparison_events(self):
        code, out, err, order = self.execute(["--runs", "3"], [fixture()] * 7)
        self.assertEqual(code, 0)
        self.assertEqual(order, ["--health-report", "snapshot"] +
                         ["--start", "snapshot", "--health-report", "snapshot"] * 3)
        self.assertIn("Finished 4/4 stages", out)
        self.assertIn("Pre-health compare container identities and restarts  DONE", out)
        self.assertIn("Post-health compare migration history  DONE", out)
        self.assertEqual(out.count("RESULT: STARTUP IDEMPOTENCY VERIFIED"), 1)
        self.assertIn("PASS: attempt 3/3", out)
        self.assertNotIn("id:transcendence", out)
        self.assertNotIn("\x1b", out)
        self.assertEqual(err, "")

    def test_check_only_baseline_no_start(self):
        code, out, err, order = self.execute(["--check-only"], [fixture()])
        self.assertEqual(code, 0)
        self.assertEqual(order, ["--health-report", "snapshot"])
        self.assertIn("Finished 1/1 stages", out)
        self.assertIn("RESULT: IDEMPOTENCY BASELINE READY (read-only)", out)

    def test_initialize_is_explicit_and_preserves_order(self):
        code, out, err, order = self.execute(["--initialize", "--runs", "2"], [fixture()] * 5)
        self.assertEqual(code, 0)
        self.assertEqual(order, ["--health-report", "snapshot"] +
                         ["--initialize", "snapshot", "--health-report", "snapshot"] * 2)
        self.assertIn("Finished 3/3 stages", out)
        self.assertIn("RESULT: INITIALIZE IDEMPOTENCY VERIFIED", out)
        self.assertNotIn("RESULT: STARTUP IDEMPOTENCY VERIFIED", out)

    def test_pre_health_restart_drift_fails_before_health_and_next_attempt(self):
        changed = fixture()
        name = acceptance.CONTAINERS[0]
        old = changed["containers"][name]
        changed["containers"][name] = (old[0], old[1], 1, "new start")
        code, out, err, order = self.execute(["--runs", "3"], [fixture(), changed])
        self.assertEqual(code, 1)
        self.assertEqual(order, ["--health-report", "snapshot", "--start", "snapshot"])
        self.assertIn("Pre-health compare container identities and restarts  FAILED", out)
        self.assertNotIn("Startup attempt 2 of 3", out)
        self.assertNotIn("STARTUP IDEMPOTENCY VERIFIED", out)
        self.assertIn("RESULT: STARTUP ACCEPTANCE STOPPED", err)

    def test_post_health_migration_drift_fails_before_next_attempt(self):
        changed = fixture()
        changed["migration_history"] = "b" * 32
        code, out, err, order = self.execute(["--runs", "3"],
                                            [fixture(), fixture(), changed])
        self.assertEqual(code, 1)
        self.assertEqual(order, ["--health-report", "snapshot", "--start",
                                 "snapshot", "--health-report", "snapshot"])
        self.assertIn("Post-health compare migration history  FAILED", out)
        self.assertNotIn("Startup attempt 2 of 3", out)
        self.assertNotIn("STARTUP IDEMPOTENCY VERIFIED", out)

    def test_unhealthy_baseline_refuses_all_starts_and_snapshots(self):
        code, out, err, order = self.execute(["--runs", "3"], [],
                                            trusted_error="--health-report")
        self.assertEqual(code, 1)
        self.assertEqual(order, ["--health-report"])
        self.assertIn("Healthy baseline FAILED", out)
        self.assertIn("RESULT: STARTUP ACCEPTANCE STOPPED", err)

    def test_invalid_runs_does_not_begin_renderer_or_touch_system(self):
        code, out, err, order = self.execute(["--runs", "0"], [])
        self.assertEqual(code, 1)
        self.assertEqual(order, [])
        self.assertNotIn("Verifying startup idempotency", out)
        self.assertIn("Runs must be between 1 and 5", err)


if __name__ == "__main__":
    unittest.main()
