"""Offline tests for the recovery-material cancellation boundary."""
import os
import signal
import subprocess
import sys
import unittest

from bootstrap_interrupt import DeferredInterrupt, defer_termination


class DeferredTerminationTests(unittest.TestCase):
    def test_no_signal_completes(self):
        with defer_termination():
            result = "persisted"
        self.assertEqual(result, "persisted")

    def test_interrupt_is_deferred_and_handlers_restored(self):
        original = signal.getsignal(signal.SIGINT)
        completed = []
        with self.assertRaises(DeferredInterrupt) as caught:
            with defer_termination():
                os.kill(os.getpid(), signal.SIGINT)
                completed.append("fsync")
        self.assertEqual(completed, ["fsync"])
        self.assertEqual(caught.exception.signum, signal.SIGINT)
        self.assertIs(signal.getsignal(signal.SIGINT), original)

    def test_first_signal_wins(self):
        original = signal.getsignal(signal.SIGTERM)
        with self.assertRaises(DeferredInterrupt) as caught:
            with defer_termination():
                os.kill(os.getpid(), signal.SIGTERM)
                os.kill(os.getpid(), signal.SIGINT)
        self.assertEqual(caught.exception.signum, signal.SIGTERM)
        self.assertEqual(signal.getsignal(signal.SIGTERM), original)

    def test_persistence_failure_takes_precedence(self):
        with self.assertRaisesRegex(OSError, "fsync failed"):
            with defer_termination():
                os.kill(os.getpid(), signal.SIGINT)
                raise OSError("fsync failed")

    def test_process_group_interrupt_does_not_kill_isolated_child(self):
        code = '''import os, signal, subprocess, sys
from bootstrap_interrupt import DeferredInterrupt, defer_termination
try:
    with defer_termination():
        child = subprocess.Popen([sys.executable, '-c',
            'import time; time.sleep(0.2); print("persisted", flush=True)'],
            stdout=subprocess.PIPE, start_new_session=True)
        os.killpg(os.getpgrp(), signal.SIGINT)
        output, _ = child.communicate(timeout=3)
        assert child.returncode == 0
        assert output == b'persisted\\n'
except DeferredInterrupt as exc:
    sys.exit(128 + exc.signum)
raise AssertionError('signal not delivered')
'''
        result = subprocess.run([sys.executable, '-c', code],
                                start_new_session=True, capture_output=True,
                                timeout=5, check=False)
        self.assertEqual(result.returncode, 130, result.stderr.decode())


if __name__ == "__main__":
    unittest.main()
