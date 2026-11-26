"""Offline presentation contracts for the explicit Vault UI commands."""

from contextlib import contextmanager, redirect_stdout, redirect_stderr
import copy
import io
import os
import unittest
from unittest.mock import patch

import bootstrap_unified as unified
import bootstrap_vault_ui as ui
from test_bootstrap_vault_ui import fixture
from test_bootstrap_progress import Terminal


def stopped():
    item = fixture()
    item["State"] = {"Running": False, "Status": "exited"}
    return item


class VaultUIProgressTests(unittest.TestCase):
    def execute(self, mode, *, item=None, enabled=False, progress=True,
                probe_error=False, lock_error=False, stop_drift=False, env=None):
        state = {"item": copy.deepcopy(item), "enabled": enabled}
        calls = []

        @contextmanager
        def lock():
            calls.append("lock")
            if lock_error:
                raise unified.UnifiedStartupError("busy")
            try:
                yield
            finally:
                calls.append("unlock")

        def inspect():
            calls.append("inspect")
            return copy.deepcopy(state["item"])

        def mark_enabled():
            calls.append("enable intent")
            changed = not state["enabled"]
            state["enabled"] = True
            return changed

        def mark_disabled():
            calls.append("disable intent")
            changed = state["enabled"]
            state["enabled"] = False
            return changed

        def command(args, **kwargs):
            if args == [ui.sys.executable, str(ui.ROOT / "scripts/dev/bootstrap_vault.py"), "check"]:
                calls.append("vault check")
            elif args == ["docker", "start", ui.NAME]:
                calls.append("start")
                state["item"]["State"] = {"Running": True, "Status": "running"}
            elif args == ["docker", "stop", "--time", "10", ui.NAME]:
                calls.append("stop")
                state["item"]["State"] = {"Running": False, "Status": "exited"}
                if stop_drift:
                    state["item"]["Id"] = "changed"
            elif args == ["docker", "compose", "up", "-d", "--no-deps", "--no-recreate", ui.SERVICE]:
                calls.append("create")
                state["item"] = fixture()
            else:
                self.fail("Unexpected command: " + repr(args))
            return ""

        def probe():
            calls.append("HTTPS")
            if probe_error:
                raise ui.GatewayError("Vault Web UI TLS/HTTP probe failed")

        args = ["bootstrap_vault_ui.py", mode] + (["--progress"] if progress else [])
        with (
            patch.object(ui.os, "geteuid", return_value=1000),
            patch.object(unified, "exclusive_orchestrator_lock", side_effect=lock),
            patch.object(ui, "compose", return_value=(["docker", "compose"], {})),
            patch.object(ui, "inspect", side_effect=inspect),
            patch.object(ui, "verify_network"),
            patch.object(ui, "desired_enabled", side_effect=lambda: state["enabled"]),
            patch.object(ui, "mark_enabled", side_effect=mark_enabled),
            patch.object(ui, "mark_disabled", side_effect=mark_disabled),
            patch.object(ui, "command", side_effect=command),
            patch.object(ui, "probe_ui", side_effect=probe),
            patch.dict(os.environ, {"CI": "1", "TERM": "xterm"} if env is None else env, clear=True),
            patch("sys.argv", args),
            redirect_stdout(Terminal()) as out,
            redirect_stderr(Terminal()) as err,
        ):
            code = ui.main()
        return code, out.getvalue(), err.getvalue(), calls, state

    def test_enable_creates_only_gateway_after_recording_intent(self):
        code, out, err, calls, state = self.execute("enable")
        self.assertEqual(code, 0)
        self.assertEqual(calls, ["lock", "inspect", "enable intent", "vault check",
                                 "inspect", "create", "inspect", "HTTPS", "unlock"])
        self.assertTrue(state["enabled"])
        self.assertIn("Finished 6/6 stages", out)
        self.assertIn("CREATED: " + ui.NAME, out)
        self.assertIn("RESULT: VAULT UI ENABLED", out)
        self.assertIn(ui.UI_URL, out)
        self.assertEqual(err, "")

    def test_enable_resumes_without_recreating(self):
        code, out, _, calls, state = self.execute("enable", item=stopped())
        self.assertEqual(code, 0)
        self.assertIn("start", calls)
        self.assertNotIn("create", calls)
        self.assertEqual(state["item"]["Id"], fixture()["Id"])
        self.assertIn("START: " + ui.NAME, out)
        self.assertEqual(calls.count("HTTPS"), 1)

    def test_already_enabled_keeps_gateway_and_intent(self):
        code, out, _, calls, _ = self.execute("enable", item=fixture(), enabled=True)
        self.assertEqual(code, 0)
        self.assertIn("Record enabled intent CACHED", out)
        self.assertIn("Prepare gateway CACHED", out)
        self.assertIn("KEEP: " + ui.NAME, out)
        self.assertNotIn("start", calls)
        self.assertNotIn("create", calls)
        self.assertEqual(calls.count("HTTPS"), 1)

    def test_disable_stops_only_gateway_before_clearing_intent(self):
        code, out, err, calls, state = self.execute("disable", item=fixture(), enabled=True)
        self.assertEqual(code, 0)
        self.assertEqual(calls, ["lock", "inspect", "stop", "inspect",
                                 "disable intent", "inspect", "unlock"])
        self.assertFalse(state["enabled"])
        self.assertIn("Finished 5/5 stages", out)
        self.assertIn("STOP: " + ui.NAME, out)
        self.assertIn("RESULT: VAULT UI DISABLED", out)
        self.assertNotIn(ui.UI_URL, out)
        self.assertEqual(err, "")

    def test_already_disabled_stopped_or_absent(self):
        for item in (stopped(), None):
            with self.subTest(item=item is not None):
                code, out, _, calls, _ = self.execute("disable", item=item)
                self.assertEqual(code, 0)
                self.assertIn("Stop or keep gateway CACHED", out)
                self.assertIn("Clear enabled intent CACHED", out)
                self.assertNotIn("stop", calls)
                self.assertNotIn("HTTPS", calls)
                self.assertNotIn(ui.UI_URL, out)

    def test_status_enabled_is_read_only_and_shows_verified_url(self):
        code, out, _, calls, _ = self.execute("status", item=fixture(), enabled=True)
        self.assertEqual(code, 0)
        self.assertEqual(calls, ["inspect", "HTTPS"])
        self.assertIn("Finished 2/2 stages", out)
        self.assertIn("Desired state: ENABLED", out)
        self.assertIn("Gateway state: running", out)
        self.assertIn("STATUS: VAULT UI ENABLED (healthy)", out)
        self.assertIn(ui.UI_URL, out)

    def test_status_disabled_is_healthy_without_probe(self):
        for item, actual in ((stopped(), "stopped"), (None, "absent")):
            with self.subTest(actual=actual):
                code, out, _, calls, _ = self.execute("status", item=item)
                self.assertEqual(code, 0)
                self.assertEqual(calls, ["inspect"])
                self.assertIn("Desired state: DISABLED", out)
                self.assertIn("Gateway state: " + actual, out)
                self.assertIn("STATUS: VAULT UI DISABLED (expected)", out)
                self.assertNotIn(ui.UI_URL, out)

    def test_untrusted_identity_refused_before_intent_or_lifecycle(self):
        item = fixture()
        item["HostConfig"]["Privileged"] = True
        for mode in ("enable", "disable"):
            with self.subTest(mode=mode):
                code, out, err, calls, _ = self.execute(mode, item=item)
                self.assertEqual(code, 1)
                self.assertEqual(calls, ["lock", "inspect", "unlock"])
                self.assertIn("Inspect gateway configuration FAILED", out)
                self.assertIn("RESULT: VAULT UI GATEWAY REFUSED", err)
                self.assertNotIn(ui.UI_URL, out)

    def test_failed_https_keeps_enabled_intent_and_withholds_url(self):
        code, out, err, calls, state = self.execute("enable", item=stopped(), probe_error=True)
        self.assertEqual(code, 1)
        self.assertTrue(state["enabled"])
        self.assertEqual(calls[-2:], ["HTTPS", "unlock"])
        self.assertIn("Verify HTTPS readiness FAILED", out)
        self.assertIn("Vault Web UI TLS/HTTP probe failed", err)
        self.assertNotIn("RESULT: VAULT UI ENABLED", out)
        self.assertNotIn(ui.UI_URL, out)

    def test_enabled_status_probe_failure_withholds_url_and_success(self):
        code, out, err, calls, _ = self.execute("status", item=fixture(), enabled=True, probe_error=True)
        self.assertEqual(code, 1)
        self.assertEqual(calls, ["inspect", "HTTPS"])
        self.assertIn("Verify actual gateway state FAILED", out)
        self.assertIn("RESULT: VAULT UI GATEWAY REFUSED", err)
        self.assertNotIn("STATUS: VAULT UI ENABLED", out)
        self.assertNotIn(ui.UI_URL, out)

    def test_status_refuses_drift_without_repair(self):
        for item, enabled in ((fixture(), False), (None, True), (stopped(), True)):
            with self.subTest(enabled=enabled, present=item is not None):
                code, out, err, calls, _ = self.execute("status", item=item, enabled=enabled)
                self.assertEqual(code, 1)
                self.assertEqual(calls, ["inspect"])
                self.assertIn("RESULT: VAULT UI GATEWAY REFUSED", err)
                self.assertNotIn("STATUS:", out)
                self.assertNotIn(ui.UI_URL, out)

    def test_stop_identity_drift_preserves_enabled_intent(self):
        code, out, err, calls, state = self.execute("disable", item=fixture(), enabled=True, stop_drift=True)
        self.assertEqual(code, 1)
        self.assertTrue(state["enabled"])
        self.assertNotIn("disable intent", calls)
        self.assertIn("Stop or keep gateway FAILED", out)
        self.assertIn("Gateway identity changed during stop", err)

    def test_lock_contention_prevents_inspection_or_mutation(self):
        for mode in ("enable", "disable"):
            with self.subTest(mode=mode):
                code, out, err, calls, _ = self.execute(mode, lock_error=True)
                self.assertEqual(code, 1)
                self.assertEqual(calls, ["lock"])
                self.assertIn("Acquire startup lock FAILED", out)
                self.assertIn("Trusted startup lock unavailable", err)

    def test_legacy_internal_status_keeps_original_markers_and_no_renderer(self):
        code, out, err, calls, _ = self.execute("status", progress=False)
        self.assertEqual(code, 0)
        self.assertEqual(calls, ["inspect"])
        self.assertEqual(out.splitlines(), ["RESULT: VAULT UI DISABLED (expected, read-only)",
                                            "STATUS: VAULT UI DISABLED (expected)"])
        self.assertEqual(err, "")

    def test_plain_flags_and_verbose_keep_safe_output(self):
        for flag in ("CI", "NO_COLOR", "FT_DEV_BOOTSTRAP_VERBOSE", "FT_DEV_STAGE_TIMING"):
            with self.subTest(flag=flag):
                code, out, err, _, _ = self.execute("status", item=fixture(), enabled=True,
                                                    env={"TERM": "xterm", flag: "1"})
                self.assertEqual(code, 0)
                self.assertNotIn("\x1b", out + err)
                self.assertNotIn("\r", out + err)
                self.assertEqual(out.count("STATUS: VAULT UI ENABLED (healthy)"), 1)
                self.assertNotIn("trusted-gateway-id", out)
                if flag == "FT_DEV_BOOTSTRAP_VERBOSE":
                    self.assertIn("Probe trusted HTTPS UI  DONE", out)
                    self.assertIn("RESULT: VAULT UI GATEWAY READY (read-only)", out)


if __name__ == "__main__":
    unittest.main()
