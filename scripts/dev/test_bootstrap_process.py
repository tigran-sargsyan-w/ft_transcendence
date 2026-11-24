import os
import signal
import sys
import tempfile
import threading
import unittest
from pathlib import Path

from bootstrap_process import Cancellation, ChildRunner, Diagnostics, InitializationCancelled


class Display:
    def __init__(self):
        self.notices = []
        self.refreshes = 0
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
