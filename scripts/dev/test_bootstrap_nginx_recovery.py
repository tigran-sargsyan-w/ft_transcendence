"""Issue #155: offline recovery-only Nginx readiness boundaries."""
import contextlib
import io
import signal
import subprocess
import unittest
from unittest.mock import MagicMock, call, patch

import bootstrap_nginx as nginx
import bootstrap_unified as unified


ID = "a" * 64
TARGETS = ((nginx.NAME, ID, nginx.verify_identity, "running"),)


class NginxRecoveryTests(unittest.TestCase):
    def test_only_explicit_recovery_waits_for_running_nginx(self):
        with patch.object(unified, "checked", return_value=False), \
             patch.object(unified, "guard_resume",
                          side_effect=unified.RunningNotReady("nginx", TARGETS)), \
             patch.object(unified, "wait_recovery_nginx") as wait, \
             patch.object(unified, "start_existing_only") as start:
            with self.assertRaises(unified.RunningNotReady):
                unified.resume("nginx")
            wait.assert_not_called()
            unified.resume("nginx", recovery=True)
            wait.assert_called_once_with(TARGETS)
            start.assert_not_called()

    def test_healthy_nginx_has_no_extra_probe(self):
        with patch.object(unified, "checked", return_value=True), \
             patch.object(unified, "guard_resume") as guard, \
             patch.object(unified, "wait_recovery_nginx") as wait, \
             contextlib.redirect_stdout(io.StringIO()):
            unified.resume("nginx", recovery=True)
        guard.assert_not_called()
        wait.assert_not_called()

    def test_guard_failure_never_enters_wait(self):
        with patch.object(unified, "checked", return_value=False), \
             patch.object(unified, "guard_resume", side_effect=unified.UnifiedStartupError("untrusted")), \
             patch.object(unified, "wait_recovery_nginx") as wait:
            with self.assertRaisesRegex(unified.UnifiedStartupError, "untrusted"):
                unified.resume("nginx", recovery=True)
        wait.assert_not_called()

    def test_recovery_slow_path_passes_flag_only_to_nginx_and_agent(self):
        with patch.object(unified, "checked", side_effect=lambda n: n != "nginx"), \
             patch.object(unified, "resume") as resume, \
             patch.object(unified, "vault_seal_guard"), \
             contextlib.redirect_stdout(io.StringIO()):
            unified.start(recovery=True)
        self.assertEqual(resume.call_args_list, [
            call(n, recovery=True) if n in ("nginx", "backend-agent") else call(n)
            for n in unified.RESUMABLE
        ])

    def test_502_then_ready_with_remaining_wall_clock_budget(self):
        with patch.object(unified.time, "monotonic", side_effect=[0, 0, 5, 5, 7, 12]), \
             patch.object(unified.time, "sleep") as sleep, \
             patch.object(unified, "recovery_nginx_probe", side_effect=[20, 0]) as probe, \
             contextlib.redirect_stdout(io.StringIO()) as output:
            unified.wait_recovery_nginx(TARGETS)
        self.assertEqual(probe.call_args_list, [call(ID, 60), call(ID, 53)])
        sleep.assert_called_once_with(2)
        self.assertIn("HTTP 502", output.getvalue())

    def test_nonretryable_codes_stop_without_sleep(self):
        for code in (1, 2, 3, -9, 99):
            with self.subTest(code=code), \
                 patch.object(unified, "recovery_nginx_probe", return_value=code) as probe, \
                 patch.object(unified.time, "sleep") as sleep:
                with self.assertRaisesRegex(unified.UnifiedStartupError, "outside retryable"):
                    unified.wait_recovery_nginx(TARGETS)
                probe.assert_called_once()
                sleep.assert_not_called()

    def test_persistent_502_exhausts_budget(self):
        with patch.object(unified.time, "monotonic", side_effect=[0, 0, 58, 58, 60]), \
             patch.object(unified.time, "sleep") as sleep, \
             patch.object(unified, "recovery_nginx_probe", return_value=20) as probe, \
             contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(unified.UnifiedStartupError, "budget expired"):
                unified.wait_recovery_nginx(TARGETS)
        probe.assert_called_once_with(ID, 60)
        sleep.assert_called_once_with(2)

    def test_success_after_deadline_is_rejected(self):
        with patch.object(unified.time, "monotonic", side_effect=[0, 0, 61]), \
             patch.object(unified, "recovery_nginx_probe", return_value=0), \
             patch.object(unified.time, "sleep") as sleep:
            with self.assertRaisesRegex(unified.UnifiedStartupError, "budget expired"):
                unified.wait_recovery_nginx(TARGETS)
        sleep.assert_not_called()

    def test_invalid_targets_never_launch_probe(self):
        for targets in ((), TARGETS * 2, (("wrong", ID, None, "running"),),
                        ((nginx.NAME, "short", None, "running"),),
                        ((nginx.NAME, ID, None, "stopped"),)):
            with self.subTest(targets=targets), \
                 patch.object(unified, "recovery_nginx_probe") as probe:
                with self.assertRaises(unified.UnifiedStartupError):
                    unified.wait_recovery_nginx(targets)
                probe.assert_not_called()

    def test_probe_has_fixed_readonly_argv_and_no_raw_output(self):
        child = MagicMock()
        child.wait.return_value = 20
        with patch.object(unified.subprocess, "Popen", return_value=child) as launch:
            self.assertEqual(unified.recovery_nginx_probe(ID, 7), 20)
        self.assertEqual(launch.call_args.args[0], [
            unified.sys.executable, str(unified.ROOT / "scripts/dev/bootstrap_nginx.py"),
            "check-recovery", "--expected-id", ID,
        ])
        self.assertTrue(launch.call_args.kwargs["start_new_session"])
        for key in ("stdin", "stdout", "stderr"):
            self.assertEqual(launch.call_args.kwargs[key], subprocess.DEVNULL)
        child.wait.assert_called_once_with(timeout=7)

    def test_timeout_and_interrupt_stop_local_probe_group(self):
        for exc in (subprocess.TimeoutExpired("probe", 7), KeyboardInterrupt()):
            child = MagicMock(pid=4321)
            child.wait.side_effect = [exc, -9]
            expected = KeyboardInterrupt if isinstance(exc, KeyboardInterrupt) else unified.UnifiedStartupError
            with self.subTest(exc=type(exc)), \
                 patch.object(unified.subprocess, "Popen", return_value=child), \
                 patch.object(unified.os, "killpg") as kill:
                with self.assertRaises(expected):
                    unified.recovery_nginx_probe(ID, 7)
                kill.assert_called_once_with(4321, signal.SIGKILL)
                self.assertEqual(child.wait.call_args_list, [call(timeout=7), call()])


if __name__ == "__main__":
    unittest.main()
