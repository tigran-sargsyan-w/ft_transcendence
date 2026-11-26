"""Offline presentation tests: no live Docker, host changes or real Shamir input."""
from contextlib import ExitStack, nullcontext, redirect_stdout, redirect_stderr
import io
import os
import subprocess
import sys
from threading import Event
import unittest
from unittest.mock import patch

import bootstrap_cli_progress as cli
import bootstrap_unified as unified
from bootstrap_progress import Phase, Status
from scripts.dev import bootstrap_host_check as host_check


class Terminal(io.StringIO):
    def isatty(self):
        return True


class PresentationTests(unittest.TestCase):
    def test_plain_events_and_result_order(self):
        output = io.StringIO()
        with cli.presentation(("Probe", "Resume"), "Starting test", stream=output,
                              env={"CI": "true"}, allow_not_ready=True):
            with cli.stage("Probe") as outcome:
                with cli.step("Readiness probe") as step:
                    step.status = Status.NOT_READY
                outcome.status = Status.NOT_READY
            with cli.stage("Resume", Phase.VERIFY):
                cli.result("TEST READY")
        log = output.getvalue()
        self.assertIn("Probe NOT READY", log)
        self.assertIn("Resume DONE", log)
        self.assertLess(log.index("Finished 2/2"), log.index("RESULT: TEST READY"))
        self.assertNotIn("\x1b", log)
        self.assertNotIn("\r", log)

    def test_failure_stops_later_stages_and_drops_queued_results(self):
        output = io.StringIO()
        with self.assertRaisesRegex(RuntimeError, "refused"):
            with cli.presentation(("Probe", "Later"), "Checking test", stream=output):
                with cli.stage("Probe"):
                    cli.result("MUST NOT APPEAR")
                    raise RuntimeError("refused")
        log = output.getvalue()
        self.assertIn("Probe FAILED", log)
        self.assertIn("Finished 0/2", log)
        self.assertNotIn("RESULT: MUST NOT APPEAR", log)
        self.assertNotIn("Later DONE", log)
        self.assertIsNone(cli._current.get())

    def test_interrupt_closes_active_stage(self):
        output = io.StringIO()
        with self.assertRaises(KeyboardInterrupt):
            with cli.presentation(("Probe", "Later"), "Checking test", stream=output):
                with cli.stage("Probe"):
                    raise KeyboardInterrupt
        self.assertIn("Probe CANCELLED", output.getvalue())
        self.assertIn("Finished 0/2", output.getvalue())
        self.assertIsNone(cli._current.get())

    def test_ticker_refreshes_and_joins(self):
        output = Terminal()
        tick = Event()
        with patch("bootstrap_progress.sys.stderr.isatty", return_value=True):
            with cli.presentation(("Probe",), "Checking test", stream=output,
                                  env={"TERM": "xterm"},
                                  terminal_width=lambda: 100,
                                  terminal_height=lambda: 12) as session:
                original = session.progress.refresh

                def refresh(*, force=False):
                    original(force=force)
                    if not force and session.progress.active is not None:
                        tick.set()

                with patch.object(session.progress, "refresh", side_effect=refresh):
                    with cli.stage("Probe"):
                        self.assertTrue(tick.wait(2), "timer never refreshed")
                self.assertTrue(session.ticker.is_alive())
            self.assertFalse(session.ticker.is_alive())
        self.assertEqual(session.progress.drawn, 0)

    def test_interactive_pause_owns_no_terminal_rows(self):
        output = Terminal()
        with patch("bootstrap_progress.sys.stderr.isatty", return_value=True):
            with cli.presentation(("Unseal",), "Recovering test", stream=output,
                                  env={"TERM": "xterm"},
                                  terminal_width=lambda: 100,
                                  terminal_height=lambda: 12) as session:
                with cli.stage("Unseal"):
                    with cli.interactive("Manual Shamir unseal"):
                        self.assertTrue(session.progress.paused)
                        self.assertEqual(session.progress.drawn, 0)
                        before = output.getvalue()
                        with session.lock:
                            session.progress.refresh(force=True)
                        self.assertEqual(output.getvalue(), before)
                    self.assertFalse(session.progress.paused)
        self.assertIn("Manual Shamir unseal", output.getvalue())
        self.assertIn("DONE", output.getvalue())

    def test_failed_interaction_does_not_resume_repaint(self):
        output = Terminal()
        with patch("bootstrap_progress.sys.stderr.isatty", return_value=True):
            with self.assertRaises(RuntimeError):
                with cli.presentation(("Unseal", "Application"), "Recovering test",
                                      stream=output, env={"TERM": "xterm"},
                                      terminal_width=lambda: 100,
                                      terminal_height=lambda: 12) as session:
                    with cli.stage("Unseal"):
                        with cli.interactive("Manual Shamir unseal"):
                            raise RuntimeError("refused")
        self.assertTrue(session.progress.paused)
        self.assertEqual(session.progress.drawn, 0)
        self.assertIs(session.progress.stages[0].status, Status.FAILED)
        self.assertIs(session.progress.stages[1].status, Status.PENDING)
        self.assertFalse(session.ticker.is_alive())


class RoutinePresentationTests(unittest.TestCase):
    def setUp(self):
        self.output = io.StringIO()
        self.errors = io.StringIO()
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(redirect_stdout(self.output))
        self.stack.enter_context(redirect_stderr(self.errors))
        self.stack.enter_context(patch.object(unified.os, "geteuid", return_value=1000))
        self.preflight = self.stack.enter_context(patch.object(unified, "preflight_once"))
        self.lock = self.stack.enter_context(patch.object(
            unified, "exclusive_orchestrator_lock", side_effect=lambda: nullcontext()))
        self.guard = self.stack.enter_context(patch.object(unified, "vault_seal_guard"))
        self.stack.enter_context(patch.dict(os.environ, {
            "CI": "true", "FT_DEV_BOOTSTRAP_VERBOSE": "", "FT_DEV_STAGE_TIMING": "",
        }))

    def run_mode(self, mode, *args):
        return unified.main([mode, *args], render=True)

    def test_health_and_check_all_keep_distinct_fast_path_order(self):
        for mode, ordered, marker in (
            ("health-report", ["host", "resources", "nginx", "vault-ui-optional",
                               "prometheus", "grafana", "grafana-https"],
             "DEVELOPMENT HEALTHY (read-only)"),
            ("check", ["host", "resources", "vault-ui-optional", "nginx",
                       "prometheus", "grafana", "grafana-https"],
             "UNIFIED STACK READY (read-only)"),
        ):
            with self.subTest(mode=mode), patch.object(unified, "command", return_value=True) as cmd:
                self.output.seek(0)
                self.output.truncate()
                self.assertEqual(self.run_mode(mode), 0)
                self.assertEqual([c.args[0] for c in cmd.call_args_list],
                                 ["--check-" + name for name in ordered])
                self.assertTrue(all(c.kwargs == {"quiet": True} for c in cmd.call_args_list))
                log = self.output.getvalue()
                self.assertIn("Finished 8/8 stages", log)
                self.assertIn("RESULT: " + marker, log)
                self.assertNotIn("\x1b", log)
        self.lock.assert_not_called()

    def test_health_localizes_failure_without_mutation(self):
        with patch.object(unified, "checked", side_effect=lambda n: n not in ("nginx", "postgres")) as probe:
            self.assertEqual(self.run_mode("health-report"), 1)
        self.assertEqual([c.args[0] for c in probe.call_args_list],
                         ["host", "resources", "nginx", "vault", "vault-ui",
                          "vault-config", "vault-secrets", "postgres"])
        self.assertIn("FAIL: postgres; inspect --check-postgres", self.output.getvalue())
        self.assertIn("Application HTTPS FAILED", self.output.getvalue())
        self.assertNotIn("DEVELOPMENT HEALTHY", self.output.getvalue())
        self.assertIn("UNIFIED BOOTSTRAP STOPPED", self.errors.getvalue())
        self.lock.assert_not_called()

    def test_health_intermittent_second_pass_is_still_failure(self):
        values = [True, True, False] + [True] * (len(unified.STAGES) - 2)
        with patch.object(unified, "checked", side_effect=values) as probe:
            self.assertEqual(self.run_mode("health-report"), 1)
        self.assertEqual(probe.call_count, len(unified.STAGES) + 1)
        self.assertIn("Intermittent readiness failure", self.errors.getvalue())
        self.assertNotIn("DEVELOPMENT HEALTHY", self.output.getvalue())

    def test_start_healthy_fast_path_does_not_resume_or_prepare(self):
        with patch.object(unified, "command", return_value=True) as cmd, \
             patch.object(unified, "resume") as resume:
            self.assertEqual(self.run_mode("start"), 0)
        self.assertEqual([c.args[0] for c in cmd.call_args_list],
                         ["--check-host", "--check-resources", "--check-vault-ui-optional",
                          "--check-nginx", "--check-prometheus", "--check-grafana",
                          "--check-grafana-https"])
        resume.assert_not_called()
        self.guard.assert_called_once_with()
        self.lock.assert_called_once_with()
        log = self.output.getvalue()
        self.assertIn("Application readiness CACHED", log)
        self.assertIn("Resume existing services SKIPPED", log)
        self.assertIn("RESULT: UNIFIED STARTUP READY", log)

    def test_start_optional_ui_failure_stays_nonfatal(self):
        with patch.object(unified, "checked", side_effect=lambda n: n != "vault-ui"):
            self.assertEqual(self.run_mode("start"), 0)
        log = self.output.getvalue()
        self.assertIn("Optional Vault UI NOT READY", log)
        self.assertIn("WARN: optional Vault UI state unhealthy", log)
        self.assertIn("RESULT: UNIFIED STARTUP READY", log)

    def test_start_slow_path_preserves_resume_order(self):
        with patch.object(unified, "checked", side_effect=lambda n: n != "nginx"), \
             patch.object(unified, "resume") as resume:
            self.assertEqual(self.run_mode("start"), 0)
        self.assertEqual([c.args[0] for c in resume.call_args_list], list(unified.RESUMABLE))
        self.assertTrue(all(not c.kwargs for c in resume.call_args_list))
        log = self.output.getvalue()
        self.assertIn("Application readiness NOT READY", log)
        self.assertIn("Resume existing services DONE", log)

    def test_recover_pauses_only_for_sealed_vault_and_emits_both_results(self):
        vault = object()
        state = {"sealed": True}
        events = []

        def prompt(*args):
            self.assertEqual(args, (vault, state))
            self.assertTrue(cli._current.get().progress.paused)
            events.append("unseal")

        def checked(name):
            if name == "vault":
                self.assertEqual(events, ["unseal"])
                self.assertFalse(cli._current.get().progress.paused)
            return True

        with patch.object(unified, "recovery_vault", return_value=(vault, state)), \
             patch.object(unified, "interactive_unseal", side_effect=prompt) as unseal, \
             patch.object(unified, "checked", side_effect=checked):
            self.assertEqual(self.run_mode("recover"), 0)
        unseal.assert_called_once_with(vault, state)
        log = self.output.getvalue()
        self.assertIn("Manual Shamir unseal DONE", log)
        self.assertLess(log.index("Finished 13/13"), log.index("RESULT: UNIFIED STARTUP READY"))
        self.assertLess(log.index("RESULT: UNIFIED STARTUP READY"),
                        log.index("RESULT: REBOOT RECOVERY READY"))

    def test_recover_already_unsealed_is_skipped(self):
        with patch.object(unified, "recovery_vault", return_value=(object(), {"sealed": False})), \
             patch.object(unified, "interactive_unseal") as unseal, \
             patch.object(unified, "checked", return_value=True):
            self.assertEqual(self.run_mode("recover"), 0)
        unseal.assert_called_once()
        self.assertIn("Manual Shamir unseal SKIPPED", self.output.getvalue())

    def test_recover_failed_prompt_never_enters_startup(self):
        with patch.object(unified, "recovery_vault", return_value=(object(), {"sealed": True})), \
             patch.object(unified, "interactive_unseal", side_effect=unified.UnifiedStartupError("refused")), \
             patch.object(unified, "checked", return_value=True), \
             patch.object(unified, "start") as start:
            self.assertEqual(self.run_mode("recover"), 1)
        start.assert_not_called()
        self.assertIn("Manual Shamir unseal FAILED", self.output.getvalue())
        self.assertNotIn("REBOOT RECOVERY READY", self.output.getvalue())

    def test_shell_entry_keeps_two_original_preflight_boundaries(self):
        events = []
        self.preflight.side_effect = lambda: events.append("shell preflight")
        with patch.object(unified.subprocess, "run", side_effect=lambda *_a, **_k:
                          (events.append("installer check") or subprocess.CompletedProcess([], 0))) as run, \
             patch.object(unified, "checked", side_effect=lambda n: (events.append(n) or True)):
            self.assertEqual(self.run_mode("check", "--shell-entry"), 0)
        self.assertEqual(events[:3], ["installer check", "shell preflight", "host"])
        self.assertEqual(run.call_args.args[0],
                         [str(unified.ROOT / "scripts/install-deps.sh"), "--check"])

    def test_shell_entry_failed_prerequisite_stops_before_other_checks(self):
        with patch.object(unified.subprocess, "run", return_value=subprocess.CompletedProcess([], 1)), \
             patch.object(unified, "checked") as checked:
            self.assertEqual(self.run_mode("check", "--shell-entry"), 1)
        self.preflight.assert_not_called()
        checked.assert_not_called()
        self.assertIn("System prerequisites FAILED", self.output.getvalue())
        self.assertIn("./scripts/install-deps.sh --check", self.errors.getvalue())

    def test_keyboard_interrupt_returns_130_and_no_ready_marker(self):
        with patch.object(unified, "checked", side_effect=KeyboardInterrupt):
            self.assertEqual(self.run_mode("health-report"), 130)
        self.assertIn("Host configuration CANCELLED", self.output.getvalue())
        self.assertNotIn("DEVELOPMENT HEALTHY", self.output.getvalue())

    def test_verbose_retry_has_safe_reviewed_details(self):
        with patch.dict(os.environ, {"FT_DEV_BOOTSTRAP_VERBOSE": "1"}), \
             patch.object(unified, "command", side_effect=[False] + [True] * 7) as cmd, \
             patch.object(unified.time, "sleep"):
            self.assertEqual(self.run_mode("check"), 0)
        self.assertEqual(cmd.call_count, 8)
        log = self.output.getvalue()
        self.assertIn("Retry host 2/3", log)
        self.assertIn("PASS: host", log)
        self.assertNotIn("\x1b", log)


class HostPresentationTests(unittest.TestCase):
    def test_host_only_checks_preserve_order_and_returncode(self):
        for codes, expected in (([0, 0, 0], 0), ([0, 2], 2)):
            with self.subTest(codes=codes), redirect_stdout(io.StringIO()) as output, \
                 redirect_stderr(io.StringIO()) as errors, \
                 patch.object(host_check.subprocess, "run", side_effect=[
                     subprocess.CompletedProcess([], code, stdout=b"SECRET-CANARY",
                                                 stderr=b"SECRET-CANARY")
                     for code in codes]) as run:
                self.assertEqual(host_check.main(), expected)
            modules = [module for _, module in host_check.CHECKS][:len(codes)]
            self.assertEqual([c.args[0] for c in run.call_args_list],
                             [[sys.executable, "-m", module, "check"] for module in modules])
            self.assertTrue(all(c.kwargs["stdout"] == subprocess.DEVNULL and
                                c.kwargs["stderr"] == subprocess.DEVNULL for c in run.call_args_list))
            log = output.getvalue() + errors.getvalue()
            self.assertNotIn("SECRET-CANARY", log)
            if expected:
                self.assertIn("scripts.dev.certificates check", log)
                self.assertNotIn("HOST CHECK READY", log)
            else:
                self.assertIn("RESULT: HOST CHECK READY (read-only)", log)


if __name__ == "__main__":
    unittest.main()
