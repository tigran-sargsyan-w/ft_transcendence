"""Offline tests for initialization progress presentation."""
import io
import unittest
from unittest.mock import patch

from bootstrap_progress import Phase, Progress, Status, emit_result


class Terminal(io.StringIO):
    def isatty(self):
        return True


def primary_output(log):
    """Return bytes sent to the permanent screen, excluding alternate frames."""
    import re
    parts = re.split(r"(\x1b\[\?1049[hl])", log)
    alternate = False
    primary = []
    for part in parts:
        if part == "\x1b[?1049h":
            if alternate:
                raise AssertionError("nested alternate screen")
            alternate = True
        elif part == "\x1b[?1049l":
            if not alternate:
                raise AssertionError("alternate screen not active")
            alternate = False
        elif not alternate:
            primary.append(part)
    if alternate:
        raise AssertionError("alternate screen left active")
    return "".join(primary)


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
        self.assertIn("\x1b[?1049h", log)
        self.assertIn("\x1b[?1049l", log)
        self.assertIn("\x1b[36mRUNNING\x1b[0m", log)
        self.assertIn("2.0s | total 2.0s", log)
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
        for line in progress._rows:
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
        progress.close()
        self.assertEqual(progress.drawn, 0)
        self.assertEqual(progress.steps, {})
        self.assertIn("\x1b[32mDONE\x1b[0m", output.getvalue())
        self.assertIn("2.0s | total 2.0s", output.getvalue())

    def test_terminal_screen_has_only_final_stage_rows(self):
        import re
        output = Terminal()
        progress = self.progress(output)
        progress.begin(0, Phase.PREPARE)
        progress.substep("Pulling image", Status.RUNNING)
        self.now = 1
        progress.refresh()
        progress.pause()
        progress.resume()
        progress.finish(Status.DONE)
        progress.begin(1, Phase.CHECK)
        progress.finish(Status.CACHED)
        progress.summary()
        screen = primary_output(output.getvalue())
        self.assertEqual(screen.count("[01/02]"), 1)
        self.assertEqual(screen.count("[02/02]"), 1)
        self.assertNotIn("Pulling image", screen)
        self.assertNotIn("PENDING", screen)
        self.assertNotIn("RUNNING", screen)

    def test_scrolling_terminal_preserves_committed_rows_and_clears_preview(self):
        import re
        output = Terminal()
        height = 8
        width = 100
        progress = Progress(tuple(f"Stage {i+1}" for i in range(21)), stream=output,
                            clock=lambda: self.now, env={"TERM": "xterm"},
                            terminal_width=lambda: width, terminal_height=lambda: height)
        for i in range(17):
            progress.begin(i, Phase.CHECK)
            for j in range(7):
                progress.substep(f"Verify operation {j+1}", Status.RUNNING)
                self.now += .3
                progress.refresh()
                progress.substep(f"Verify operation {j+1}", Status.DONE)
            if i == 5:
                progress.notice("SECURITY: Preserve recovery material.")
            progress.phase(Phase.VERIFY)
            self.now += .3
            progress.refresh()
            progress.finish(Status.CANCELLED if i == 16 else Status.DONE)
        progress.summary()

        screen = primary_output(output.getvalue())
        for i in range(1, 18):
            self.assertEqual(screen.count(f"[{i:02d}/21]"), 1)
        self.assertNotIn("[18/21]", screen)
        self.assertNotIn("PENDING", screen)
        self.assertNotIn("RUNNING", screen)
        self.assertNotIn("Verify operation", screen)
        visible = re.sub(r"\x1b\[[0-9;]*m", "", screen)
        self.assertEqual(visible.count("SECURITY: Preserve recovery material."), 1)
        self.assertEqual(screen.count("CANCELLED"), 1)
        self.assertIn("Finished 16/21", screen)

    def test_timer_diff_does_not_rewrite_pending_or_unchanged_steps(self):
        output = Terminal()
        progress = self.progress(output, width=120)
        progress.begin(0, Phase.CHECK)
        progress.substep("Verify identity", Status.DONE)
        self.now = 1
        progress.refresh()
        before = len(output.getvalue())
        progress.refresh()
        self.assertEqual(len(output.getvalue()), before)
        self.now = 2
        progress.refresh()
        delta = output.getvalue()[before:]
        self.assertNotIn("PostgreSQL", delta)
        self.assertNotIn("Verify identity", delta)
        self.assertNotIn("\x1b[2K", delta)
        self.assertIn("2.0s", delta)

    def test_width_and_status_only_color(self):
        import re
        width = 120
        progress = Progress(("Vault", "PostgreSQL"), stream=Terminal(),
                            clock=lambda: self.now, env={"TERM": "xterm"},
                            terminal_width=lambda: width, terminal_height=lambda: 12)
        progress.begin(0, Phase.CHECK)
        active, pending = progress._rows[1:3]
        self.assertEqual(active.index("\x1b["), pending.index("\x1b["))
        self.assertIn("\x1b[36mRUNNING\x1b[0m (check)", active)
        strip = lambda value: re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", value)
        self.assertEqual(len(strip(active)), 119)
        self.assertEqual(len(strip(progress._rows[-1])), 119)
        width = 65
        self.now = 1
        progress.refresh()
        self.assertTrue(all(len(strip(row)) <= 64 for row in progress._rows))

    def test_resize_redraws_only_isolated_screen_and_preserves_history(self):
        import re
        output = Terminal()
        width, height = 120, 32
        progress = Progress(("Vault", "PostgreSQL", "Backend", "Frontend"),
                            stream=output, clock=lambda: self.now,
                            env={"TERM": "xterm"}, terminal_width=lambda: width,
                            terminal_height=lambda: height)
        with progress.watch_resize():
            progress.begin(0, Phase.CHECK)
            self.now = 1
            progress.finish(Status.CACHED)
            progress.begin(1, Phase.PREPARE)
            progress.substep("Verify identity", Status.RUNNING)
            self.now = 2
            progress.refresh()
            for width, height in ((40, 7), (12, 2), (150, 45)):
                before = len(output.getvalue())
                progress.refresh()  # geometry changes bypass the frame throttle
                delta = output.getvalue()[before:]
                self.assertIn("\x1b[2J", delta)
                self.assertLessEqual(len(progress._rows), height)
                for row in progress._rows:
                    self.assertLessEqual(len(re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", row)), width-1)
            # Simulate a resize and return to original dimensions between polls.
            import signal
            signal.getsignal(signal.SIGWINCH)(signal.SIGWINCH, None)
            before = len(output.getvalue())
            progress.refresh()
            self.assertIn("\x1b[2J", output.getvalue()[before:])
            progress.notice("SECURITY: Preserve recovery material.")
            progress.finish(Status.CANCELLED)
            progress.summary()
        screen = primary_output(output.getvalue())
        self.assertEqual(screen.count("[01/04]"), 1)
        self.assertEqual(screen.count("[02/04]"), 1)
        self.assertIn("CACHED", screen)
        self.assertIn("total 1.0s", screen)
        self.assertIn("CANCELLED", screen)
        self.assertIn("SECURITY:", re.sub(r"\x1b\[[0-9;]*m", "", screen))
        self.assertNotIn("PENDING", screen)
        self.assertNotIn("Verify identity", screen)
        self.assertNotIn("\x1b[2J", screen)
        self.assertNotIn("\x1b[2K", screen)

    def test_exception_restores_screen_and_signal_handler(self):
        import signal
        old = signal.getsignal(signal.SIGWINCH)
        output = Terminal()
        progress = self.progress(output)
        with self.assertRaises(RuntimeError):
            with progress.watch_resize():
                progress.begin(0, Phase.CHECK)
                progress.finish(Status.CACHED)
                progress.begin(1, Phase.PREPARE)
                raise RuntimeError("synthetic failure")
        self.assertIs(signal.getsignal(signal.SIGWINCH), old)
        screen = primary_output(output.getvalue())
        self.assertEqual(screen.count("[01/02]"), 1)
        self.assertNotIn("RUNNING", screen)
        self.assertIn("\x1b[?25h", output.getvalue())

    def test_unsupported_terminal_uses_plain_output(self):
        output = Terminal()
        progress = self.progress(output, {"TERM": "linux"})
        progress.begin(0, Phase.CHECK)
        progress.finish(Status.CACHED)
        self.assertNotIn("\x1b", output.getvalue())

    def test_phase_replaces_previous_transient_substeps(self):
        progress = self.progress(Terminal())
        progress.begin(0, Phase.CHECK)
        progress.substep("Check previous phase", Status.NOT_READY)
        progress.phase(Phase.PREPARE)
        self.assertEqual(progress.steps, {})
        self.assertNotIn("Check previous phase", "\n".join(progress._rows))

    def test_result_color_only_on_state_and_plain_contract(self):
        output = Terminal()
        with patch("bootstrap_progress.sys.stdout", output):
            emit_result("INITIALIZATION CANCELLED (inspect state)", Status.CANCELLED,
                        env={"TERM": "xterm"})
            emit_result("UNIFIED INITIALIZATION READY", Status.DONE,
                        env={"TERM": "xterm", "NO_COLOR": ""})
        self.assertEqual(output.getvalue(),
                         "RESULT: \x1b[33mINITIALIZATION CANCELLED\x1b[0m (inspect state)\n"
                         "RESULT: UNIFIED INITIALIZATION READY\n")

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
