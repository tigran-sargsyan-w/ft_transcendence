import os
import signal
import sys
import tempfile
import threading
import unittest
from pathlib import Path

from bootstrap_process import Cancellation, ChildRunner, Diagnostics, InitializationCancelled, UnexpectedApproval
from bootstrap_progress import Status


class Display:
    def __init__(self):
        self.notices = []
        self.refreshes = 0
        self.steps = []
    def substep(self, label, status):
        self.steps.append((label, status))
    def notice(self, value):
        self.notices.append(value)
    def pause(self): pass
    def resume(self): pass
    def refresh(self): self.refreshes += 1


class ChildRunnerTests(unittest.TestCase):
    def test_fragmented_and_oversized_output(self):
        d = Diagnostics()
        d.feed(0, b'WARN: token=private-')
        d.feed(0, b'value\n' + b'x'*5000 + b'\nERROR: secret\n')
        d.finish(0)
        self.assertEqual(d.counts['warning'], 1)
        self.assertEqual(d.counts['error'], 1)
        self.assertEqual(d.counts['oversized'], 1)
        self.assertEqual(d.notices, [])

    def test_exact_safe_event_and_error_not_raw_child_output(self):
        d = Diagnostics()
        d.feed(0, b"PASS: Grafana HTTP health\nPASS: token=private-value\n")
        d.feed(1, b"ERROR: Prometheus incomplete\nERROR: secret=private-value\n")
        self.assertEqual(d.events, [("Verify Grafana HTTP health", Status.DONE)])
        self.assertEqual(d.notices, ["ERROR: Prometheus incomplete"])
        self.assertEqual(d.counts["error"], 2)

    def test_session_refresh_requires_exact_marker(self):
        d = Diagnostics()
        d.feed(0, b"ACTION REQUIRED: unknown\n")
        self.assertFalse(d.session_refresh)
        d.feed(0, b"ACTION REQUIRED: Sign out and back in (or reboot the VM), then rerun:\n")
        self.assertTrue(d.session_refresh)

    def test_pipes_drained_and_exit_preserved(self):
        display = Display()
        runner = ChildRunner(display, Cancellation())
        script = "import sys; print('token=private-value'); sys.stderr.write('ERROR: secret-value\\n'*10000); sys.exit(7)"
        self.assertEqual(runner.run([sys.executable, '-c', script], cwd=Path.cwd()), 7)
        self.assertEqual(runner.last_diagnostics.counts['stderr'], 10000)
        self.assertNotIn('secret-value', str(display.notices))
        self.assertNotIn('private-value', str(display.notices))
        self.assertGreater(display.refreshes, 0)

    def test_cancel_waits_for_active_child_and_prevents_next(self):
        cancellation = Cancellation()
        display = Display()
        runner = ChildRunner(display, cancellation)
        with cancellation.handling():
            timer = threading.Timer(.3, lambda: os.kill(os.getpid(), signal.SIGINT))
            timer.start()
            try:
                with self.assertRaises(InitializationCancelled):
                    runner.run([sys.executable, '-c', 'import signal,time; signal.signal(signal.SIGINT, signal.SIG_DFL); time.sleep(20)'], cwd=Path.cwd())
                with self.assertRaises(InitializationCancelled):
                    runner.run([sys.executable, '-c', 'raise Exception()'], cwd=Path.cwd())
            finally:
                timer.cancel()
        self.assertNotEqual(runner.last_returncode, 0)

    def test_approval_is_fatal_even_for_read_only_probe(self):
        display = Display()
        runner = ChildRunner(display, Cancellation())
        code = "print('Unexpected? [y/N]', flush=True)"
        with self.assertRaises(UnexpectedApproval):
            runner.run([sys.executable, '-c', code], cwd=Path.cwd(), probe=True)
        self.assertIn('ERROR: Unexpected initialization approval prompt; stopped safely.',
                      display.notices)

    def test_real_vault_persistence_with_isolated_mock_init_and_sigint(self):
        # Exercise the real recovery-file writer, real process groups, actual
        # signal handler and fsync; only the Vault server is simulated.
        import json
        import stat
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state/private/vault-init.json"
            code = """
import json, os, signal, sys
from pathlib import Path
from unittest.mock import patch
import bootstrap_vault as vault
path = Path(sys.argv[1])
vault.STATE, vault.RECOVERY = path.parent.parent, path
original_call = vault.call
payload = {"root_token": "synthetic-token", "unseal_keys_b64": ["a", "b", "c"]}
def fake_server(args, **kwargs):
    assert "init" in args and kwargs["isolated"]
    helper = "import os, signal, sys; os.kill(os.getppid(), signal.SIGINT); print(sys.argv[1])"
    return original_call([sys.executable, "-c", helper, json.dumps(payload)],
                         timeout=5, isolated=True)
state = {"initialized": False, "sealed": True, "n": 0, "t": 0,
         "seal_type": "shamir", "storage_type": "raft"}
with patch.object(vault, "call", side_effect=fake_server) as call, \
     patch.object(vault, "inspect_container", return_value=None), \
     patch.object(vault, "start_or_keep"), \
     patch.object(vault, "status", return_value=state):
    result = vault.main(["apply"])
    assert call.call_count == 1, "unexpected unseal/reinitialization"
    sys.exit(result)
"""
            display = Display()
            runner = ChildRunner(display, Cancellation())
            with self.assertRaises(InitializationCancelled) as exc:
                runner.run([sys.executable, "-c", code, str(path)],
                           cwd=Path(__file__).resolve().parent, critical=True)
            self.assertEqual(exc.exception.signum, signal.SIGINT)
            self.assertEqual(runner.last_returncode, 130)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)
            self.assertEqual(json.loads(path.read_text())["unseal_keys_b64"], ["a", "b", "c"])
            self.assertIn("CANCELLED: Vault recovery material persisted privately; review seal state.",
                          display.notices)
            self.assertNotIn("synthetic-token", str(display.notices) + str(display.steps))

    def test_critical_child_can_commit_after_interrupt(self):
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / 'persisted'
            ready = Path(tmp) / 'ready'
            code = ('import signal,time,sys,pathlib; '
                    'signal.signal(signal.SIGINT, lambda *_: None); '
                    'pathlib.Path(sys.argv[1]).touch(); time.sleep(.4); '
                    'pathlib.Path(sys.argv[2]).touch()')
            cancellation = Cancellation()
            runner = ChildRunner(Display(), cancellation)
            def interrupt():
                for _ in range(100):
                    if ready.exists():
                        os.kill(os.getpid(), signal.SIGINT)
                        return
                    threading.Event().wait(.01)
            with cancellation.handling():
                worker = threading.Thread(target=interrupt)
                worker.start()
                try:
                    with self.assertRaises(InitializationCancelled):
                        runner.run([sys.executable, '-c', code, str(ready), str(marker)],
                                   cwd=Path.cwd(), critical=True)
                finally:
                    worker.join()
            self.assertTrue(marker.exists())


if __name__ == '__main__':
    unittest.main()
