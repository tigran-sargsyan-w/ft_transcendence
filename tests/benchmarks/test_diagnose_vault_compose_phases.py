"""Offline tests for safe read-only Vault Compose process-phase diagnostic."""
import contextlib
import importlib.util
import io
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location(
    "diagnose_vault_compose_phases",
    Path(__file__).with_name("diagnose_vault_compose_phases.py"),
)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class PhaseTests(unittest.TestCase):
    def test_real_ordinary_subprocess_without_trace(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), \
             patch.object(mod.subprocess, "Popen", mod.PhasePopen):
            result = subprocess.run([sys.executable, "-c", "print('sensitive-canary')"],
                                    capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(output.getvalue(), "")
        self.assertIn(b"sensitive-canary", result.stdout)

    def test_tracked_subprocess_reports_phases_not_sensitive_data(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), \
             patch.object(mod, "_in_vault_helper", return_value=True), \
             patch.object(mod.subprocess, "Popen", mod.PhasePopen):
            result = subprocess.run([sys.executable, "-c", "print('SECRET-CANARY')"],
                                    input=b"SECRET-STDIN", capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0)
        log = output.getvalue()
        for fragment in ("PHASE spawn: BEGIN", "PHASE spawn: END",
                         "PHASE communicate: BEGIN", "PHASE communicate: END"):
            self.assertIn(fragment, log)
        for fragment in ("SECRET", sys.executable, "-c", "stdout", "stderr"):
            self.assertNotIn(fragment, log)

    def test_timeout_has_only_phase_and_elapsed(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), \
             patch.object(mod, "_in_vault_helper", return_value=True), \
             patch.object(mod.subprocess, "Popen", mod.PhasePopen):
            with self.assertRaises(subprocess.TimeoutExpired):
                subprocess.run([sys.executable, "-c", "import time;time.sleep(3)"],
                               input=b"SECRET-STDIN", capture_output=True, timeout=0.1)
        self.assertIn("PHASE communicate: TIMEOUT", output.getvalue())
        self.assertIn("PHASE kill: END", output.getvalue())
        self.assertNotIn("SECRET", output.getvalue())

    def test_probe_never_invokes_apply(self):
        config = Mock()
        config.main.return_value = 0
        self.assertEqual(mod.run_probe(config), 0)
        config.main.assert_called_once_with(["check"])

    def test_failed_probe_still_fails(self):
        config = Mock()
        config.main.return_value = 1
        self.assertEqual(mod.run_probe(config), 1)
        config.main.assert_called_once_with(["check"])

    def test_unexpected_error_is_not_printed_with_message(self):
        config = Mock()
        config.main.side_effect = RuntimeError("SECRET-CANARY")
        # main's catch block avoids printing raw exception content;
        # run_probe itself preserves exception semantics.
        with self.assertRaisesRegex(RuntimeError, "SECRET-CANARY"):
            mod.run_probe(config)


if __name__ == "__main__":
    unittest.main()
