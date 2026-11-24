"""Offline tests for initialization progress presentation."""
import io
import unittest
from unittest.mock import patch

from bootstrap_progress import Phase, Progress, Status


class Terminal(io.StringIO):
    def isatty(self):
        return True


class ProgressTests(unittest.TestCase):
    def setUp(self):
        self.now = 0.0
        self.stderr = patch("bootstrap_progress.sys.stderr.isatty", return_value=True)
        self.stderr.start()
        self.addCleanup(self.stderr.stop)

    def progress(self, stream, env=None, width=80):
        return Progress(("Vault", "PostgreSQL"), stream=stream,
                        clock=lambda: self.now, env={"TERM": "xterm"} if env is None else env,
                        terminal_width=lambda: width, terminal_height=lambda: 10)

    def test_plain_only_transitions_with_actual_times(self):
        output = io.StringIO()
        progress = self.progress(output)
        progress.begin(0)
        progress.phase(Phase.CHECK)
        self.now = 1.25
        progress.refresh()
        progress.phase(Phase.CHECK)
        progress.finish(Status.CACHED)
        progress.begin(1)
        progress.phase(Phase.PREPARE)
        self.now = 4.0
        progress.finish(Status.DONE)
        progress.summary()
        log = output.getvalue()
        self.assertNotIn("\x1b", log)
        self.assertNotIn("\r", log)
        self.assertEqual(log.count("RUNNING (check)"), 1)
        self.assertIn("CACHED 1.2s | total 1.2s", log)
        self.assertIn("DONE 2.8s | total 4.0s", log)
        self.assertIn("Finished 2/2 stages in 4.0s", log)

    def test_tty_repaints_only_active_line(self):
        output = Terminal()
        progress = self.progress(output)
        progress.begin(0)
        self.now = 2.0
        progress.refresh()
        progress.finish(Status.DONE)
        progress.begin(1)
        progress.finish(Status.SKIPPED)
        log = output.getvalue()
        self.assertIn("\r\x1b[2K", log)
        self.assertIn("RUNNING 2.0s", log)
        self.assertIn("\x1b[32m", log)
        self.assertIn("PENDING", log)
        self.assertTrue(log.endswith("\n"))
        self.assertEqual(progress.drawn, 0)

    def test_ci_dumb_verbose_and_timing_disable_repaint(self):
        for env in ({"TERM": "dumb"}, {"TERM": "xterm", "CI": "true"}, {"TERM": "xterm", "NO_COLOR": ""},
                    {"TERM": "xterm", "FT_DEV_BOOTSTRAP_VERBOSE": "1"},
                    {"TERM": "xterm", "FT_DEV_STAGE_TIMING": "1"}):
            with self.subTest(env=env):
                output = Terminal()
                progress = self.progress(output, env)
                progress.begin(0)
                progress.finish(Status.DONE)
                self.assertNotIn("\x1b", output.getvalue())

    def test_redirected_stderr_disables_repaint(self):
        with patch("bootstrap_progress.sys.stderr.isatty", return_value=False):
            output = Terminal()
            progress = self.progress(output)
            progress.begin(0)
            progress.finish(Status.DONE)
        self.assertNotIn("\x1b", output.getvalue())

    def test_failure_and_cancel_leave_later_stages_pending(self):
        for status in (Status.FAILED, Status.CANCELLED, Status.ACTION_REQUIRED):
            with self.subTest(status=status):
                output = io.StringIO()
                progress = self.progress(output)
                progress.begin(0)
                progress.finish(status)
                self.assertIs(progress.stages[1].status, Status.PENDING)
                with self.assertRaises(ValueError):
                    progress.begin(1)
                progress.summary()
                self.assertIn("Finished 0/2", output.getvalue())

    def test_out_of_order_and_non_structured_phase_refused(self):
        progress = self.progress(io.StringIO())
        with self.assertRaises(ValueError):
            progress.begin(1)
        progress.begin(0)
        with self.assertRaises(ValueError):
            progress.phase("unreviewed child output")
        with self.assertRaises(ValueError):
            progress.finish(Status.RUNNING)
        with self.assertRaises(ValueError):
            progress.summary()

    def test_control_characters_in_labels_refused(self):
        with self.assertRaises(ValueError):
            Progress(("Vault\x1b[2J",), stream=io.StringIO())

    def test_narrow_terminal_does_not_wrap(self):
        output = Terminal()
        progress = self.progress(output, width=20)
        progress.begin(0)
        import re
        for line in output.getvalue().split("\n"):
            clean = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", line)
            self.assertLessEqual(len(clean), 19)

    def test_pause_erases_active_region_and_substeps_are_transient(self):
        output = Terminal()
        progress = self.progress(output)
        progress.begin(0, Phase.PREPARE)
        progress.substep("Pulling image", Status.RUNNING)
        self.assertGreater(progress.drawn, 1)
        progress.pause()
        self.assertEqual(progress.drawn, 0)
        self.now = 2
        progress.resume()
        progress.substep("Pulling image", Status.DONE)
        progress.finish(Status.DONE)
        self.assertEqual(progress.drawn, 0)
        self.assertEqual(progress.steps, {})
        self.assertIn("DONE 2.0s", output.getvalue())

    def test_plain_no_duplicate_start_and_verbose_keeps_substeps(self):
        output = io.StringIO()
        progress = self.progress(output, {"FT_DEV_BOOTSTRAP_VERBOSE": "1"})
        progress.begin(0, Phase.CHECK)
        progress.substep("Verify identity", Status.DONE)
        progress.finish(Status.CACHED)
        self.assertEqual(output.getvalue().count("RUNNING"), 1)
        self.assertIn("=> => Verify identity  DONE", output.getvalue())


if __name__ == "__main__":
    unittest.main()
