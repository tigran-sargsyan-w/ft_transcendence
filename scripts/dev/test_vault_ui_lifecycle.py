"""Offline opt-in lifecycle regression and fail-closed transition tests."""
import contextlib
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import bootstrap_vault_ui as ui
from test_bootstrap_vault_ui import fixture


class VaultUiLifecycleTests(unittest.TestCase):
    def test_gateway_soft_stop_is_bounded_before_docker_timeout(self):
        config = ui.CONFIG.read_text(encoding="utf-8")
        global_section = config.split("\ndefaults\n", 1)[0]
        self.assertEqual(global_section.splitlines()[0], "global")
        directives = [line.strip() for line in global_section.splitlines()
                      if line.strip().startswith("hard-stop-after ")]
        self.assertEqual(directives, ["hard-stop-after 5s"])

    def test_unified_core_bootstrap_does_not_resume_ui(self):
        import bootstrap_unified as unified
        self.assertEqual(unified.STAGE_BY_NAME["vault-ui"], ("--check-vault-ui-optional", None))
        self.assertNotIn("vault-ui", unified.RESUMABLE)

    def test_default_disabled_without_marker(self):
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp) / "state"
            with patch.object(ui, "STATE", state), patch.object(ui, "ENABLED", state / "vault-ui.enabled"):
                self.assertFalse(ui.desired_enabled())

    def test_private_intent_roundtrip(self):
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp) / "state"
            with patch.object(ui, "STATE", state), patch.object(ui, "ENABLED", state / "vault-ui.enabled"):
                ui.mark_enabled()
                self.assertTrue(ui.desired_enabled())
                self.assertEqual(stat.S_IMODE(ui.ENABLED.stat().st_mode), 0o600)
                ui.mark_disabled()
                self.assertFalse(ui.desired_enabled())

    def test_symlink_marker_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp) / "state"
            state.mkdir(mode=0o700)
            outside = Path(temp) / "outside"
            outside.write_text("enabled\n")
            (state / "vault-ui.enabled").symlink_to(outside)
            with patch.object(ui, "STATE", state), patch.object(ui, "ENABLED", state / "vault-ui.enabled"):
                with self.assertRaises(ui.GatewayError):
                    ui.desired_enabled()
            self.assertEqual(outside.read_text(), "enabled\n")

    def test_disabled_and_missing_is_healthy(self):
        with patch.object(ui, "compose"), patch.object(ui, "inspect", return_value=None), \
             patch.object(ui, "probe_ui") as probe:
            ui.check_disabled()
            probe.assert_not_called()

    def test_disabled_and_stopped_is_healthy(self):
        item = fixture()
        item["State"] = {"Running": False, "Status": "exited"}
        with patch.object(ui, "compose"), patch.object(ui, "inspect", return_value=item), \
             patch.object(ui, "verify_network"), patch.object(ui, "probe_ui") as probe:
            ui.check_disabled()
            probe.assert_not_called()

    def test_disabled_but_running_is_failure(self):
        with patch.object(ui, "compose"), patch.object(ui, "inspect", return_value=fixture()), \
             patch.object(ui, "verify_network"):
            with self.assertRaises(ui.GatewayError):
                ui.check_disabled()

    def test_untrusted_gateway_is_never_stopped(self):
        item = fixture()
        item["HostConfig"]["Privileged"] = True
        with patch.object(ui, "compose"), patch.object(ui, "inspect", return_value=item), \
             patch.object(ui, "command") as command:
            with self.assertRaises(ui.GatewayError):
                ui.disable()
            command.assert_not_called()

    def test_stop_only_verified_gateway(self):
        running = fixture()
        stopped = fixture()
        stopped["State"] = {"Running": False, "Status": "exited"}
        with patch.object(ui, "compose"), patch.object(ui, "inspect", side_effect=[running,stopped,stopped]), \
             patch.object(ui, "verify_network"), patch.object(ui, "mark_disabled") as clear, \
             patch.object(ui, "command") as command:
            ui.disable()
            command.assert_called_once_with(["docker", "stop", "--time", "10", ui.NAME], timeout=35)
            clear.assert_called_once_with()

    def test_enable_failure_remains_enabled_for_health(self):
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp) / "state"
            with patch.object(ui, "STATE", state), patch.object(ui, "ENABLED", state / "vault-ui.enabled"), \
                 patch.object(ui, "compose"), patch.object(ui, "inspect", return_value=None), \
                 patch.object(ui, "apply", side_effect=ui.GatewayError("failed")):
                with self.assertRaises(ui.GatewayError):
                    ui.enable()
                self.assertTrue(ui.desired_enabled())


if __name__ == "__main__":
    unittest.main()
