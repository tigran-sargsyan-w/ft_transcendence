"""Offline tests for initialization progress presentation."""
import io
import unittest
from unittest.mock import patch

from bootstrap_progress import Phase, Progress, Status, emit_result


class Terminal(io.StringIO):
    def isatty(self):
        return True


def primary_output(log, *, height=100):
    """Interpret the exact inline cursor-control subset in a bounded terminal."""
    import re
    rows = ["" for _ in range(height)]
    row = col = top = 0
    for token in re.findall(r"\x1b\[[0-9;?]*[A-Za-z]|[\s\S]", log):
        if re.fullmatch(r"\x1b\[\d+A", token):
            row = max(top, row-int(token[2:-1]))
        elif re.fullmatch(r"\x1b\[\d+B", token):
            row = min(top+height-1, row+int(token[2:-1]))
        elif token == "\x1b[2K":
            rows[row] = ""
        elif token == "\x1b[K":
            rows[row] = rows[row][:col]
        elif token.startswith("\x1b"):
            if not re.fullmatch(r"\x1b\[[0-9;]*m", token):
                raise AssertionError(f"unexpected terminal command {token!r}")
        elif token == "\r":
            col = 0
        elif token == "\n":
            row += 1
            col = 0
            if row >= top+height:
                top += 1
                rows.append("")
        else:
            line = rows[row].ljust(col)
            rows[row] = line[:col]+token+line[col+1:]
            col += 1
    return "\n".join(rows)


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
        self.assertNotIn("\x1b[?1049", log)
        self.assertIn("\x1b[2K", log)
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
        self.assertEqual(progress.drawn, 0)
        self.assertFalse(progress._inline)
        self.assertNotIn("\r", output.getvalue())
        self.assertIn("Terminal too small", output.getvalue())

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
        self.assertNotIn("Final stage report", screen)

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

        screen = primary_output(output.getvalue(), height=height)
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
        active, pending = progress._rows[:2]
        self.assertEqual(active.index("\x1b["), pending.index("\x1b["))
        self.assertIn("\x1b[36mRUNNING\x1b[0m (check)", active)
        strip = lambda value: re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", value)
        self.assertEqual(len(strip(active)), 119)
        self.assertEqual(len(strip(progress._rows[-1])), 119)
        width = 65
        self.now = 1
        progress.refresh()
        self.assertEqual(progress.drawn, 0)
        self.assertFalse(progress._inline)

    def test_resize_abandons_cursor_anchor_and_appends_final_report(self):
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
            before = len(output.getvalue())
            width, height = 40, 7
            progress.refresh()
            self.assertEqual(progress.drawn, 0)
            self.assertFalse(progress._inline)
            width, height = 150, 45
            progress.refresh()
            progress.notice("SECURITY: Preserve recovery material.")
            progress.finish(Status.CANCELLED)
            progress.summary()
        tail = output.getvalue()[before:]
        self.assertNotIn("\r", tail)
        self.assertNotRegex(tail, r"\x1b\[[0-9;?]*[ABHJK]")
        self.assertNotIn("\x1b[?1049", output.getvalue())
        self.assertIn("append-only progress", tail)
        heading = "[+] Final stage report (actual results; previous progress above)\n"
        self.assertEqual(tail.count(heading), 1)
        journal, report = tail.split(heading, 1)
        self.assertIn("Final stage report follows at exit.", journal)
        self.assertIn("[02/04]", journal)
        report = re.sub(r"\x1b\[[0-9;]*m", "", report)
        self.assertEqual(report.count("[01/04]"), 1)
        self.assertEqual(report.count("[02/04]"), 1)
        self.assertIn("total 1.0s", report)
        self.assertIn("CANCELLED", report)
        self.assertNotIn("PENDING", report)
        self.assertNotIn("Verify identity", report)
        rows = [line for line in report.splitlines() if line.startswith(" =>")]
        self.assertEqual(rows[0].index("CACHED"), rows[1].index("CANCELLED"))

    def test_height_only_resize_before_notice_never_moves_cursor(self):
        output = Terminal()
        height = 32
        progress = Progress(("Vault", "PostgreSQL"), stream=output,
                            clock=lambda: self.now, env={"TERM": "xterm"},
                            terminal_width=lambda: 120, terminal_height=lambda: height)
        progress.begin(0, Phase.CHECK)
        before = len(output.getvalue())
        height = 8
        progress.notice("SECURITY: Preserve recovery material.")
        progress.finish(Status.FAILED)
        progress.summary()
        tail = output.getvalue()[before:]
        self.assertNotIn("\r", tail)
        self.assertNotRegex(tail, r"\x1b\[[0-9;?]*[ABHJK]")
        self.assertIn("Preserve recovery material.", tail)
        self.assertIn("Final stage report", tail)
        self.assertNotIn("[02/02]", tail)

    def test_geometry_changes_while_building_frame_abandon_before_write(self):
        output = Terminal()
        progress = self.progress(output, width=120)
        progress.begin(0, Phase.CHECK)
        before = len(output.getvalue())
        self.now = 1
        # refresh, first paint guard, second paint guard, last pre-write guard.
        with patch.object(progress, "_size_key", side_effect=[
                (119, 10), (119, 10), (119, 10), (79, 10)]):
            progress.refresh()
        tail = output.getvalue()[before:]
        self.assertFalse(progress._inline)
        self.assertNotIn("\r", tail)
        self.assertNotRegex(tail, r"\x1b\[[0-9;?]*[ABHJK]")

    def test_resize_signal_invalidates_anchor_even_when_geometry_returns(self):
        import signal
        output = Terminal()
        progress = self.progress(output)
        with progress.watch_resize():
            progress.begin(0, Phase.CHECK)
            before = len(output.getvalue())
            signal.getsignal(signal.SIGWINCH)(signal.SIGWINCH, None)
            progress.finish(Status.DONE)
            progress.begin(1, Phase.CHECK)
            progress.finish(Status.CACHED)
            progress.summary()
        tail = output.getvalue()[before:]
        self.assertNotIn("\r", tail)
        self.assertNotRegex(tail, r"\x1b\[[0-9;?]*[ABHJK]")
        self.assertIn("Final stage report", tail)

    def test_resize_while_paused_preserves_authentication_and_final_alignment(self):
        output = Terminal()
        width = 80
        progress = Progress(("System dependencies", "Docker images", "host"),
                            stream=output, clock=lambda: self.now,
                            env={"TERM": "xterm"}, terminal_width=lambda: width,
                            terminal_height=lambda: 32)
        with progress.watch_resize():
            progress.begin(0, Phase.CHECK)
            self.now = 1
            progress.finish(Status.CACHED)
            progress.begin(1, Phase.PREPARE)
            progress.pause()
            output.write("[sudo] Password:\n")
            width = 120
            progress.resume()
            self.now = 2
            progress.finish(Status.DONE)
            progress.begin(2, Phase.VERIFY)
            self.now = 3
            progress.finish(Status.DONE)
            progress.summary()
        screen = primary_output(output.getvalue())
        self.assertIn("[sudo] Password:", screen)
        report = screen.split("Final stage report", 1)[1]
        rows = [line for line in report.splitlines() if line.startswith(" =>")]
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0].index("CACHED"), rows[1].index("DONE"))
        self.assertEqual(rows[1].index("DONE"), rows[2].index("DONE"))
        self.assertIn("total 1.0s", rows[0])
        self.assertIn("total 2.0s", rows[1])

    def test_long_nested_labels_share_status_column_and_have_spacing(self):
        import re
        for width in (36, 50, 65, 70, 120):
            with self.subTest(width=width):
                progress = self.progress(Terminal(), width=width)
                progress.begin(0, Phase.CHECK)
                progress.substep("Verify an intentionally very long nested operation label that exceeds the available status column width", Status.DONE)
                progress.substep("Check Docker CLI", Status.RUNNING)
                self.now += 1
                progress.refresh()
                lines = [re.sub(r"\x1b\[[0-9;]*m", "", line) for line in progress._rows[:3]]
                self.assertEqual(lines[0].index("RUNNING"), lines[1].index("DONE"))
                self.assertEqual(lines[0].index("RUNNING"), lines[2].index("RUNNING"))
                self.assertTrue(all(len(line) < width for line in lines))
                self.assertIn("...", lines[1])
                self.assertIn("  DONE", lines[1])

    def test_exception_restores_inline_preview_and_signal_handler(self):
        import signal
        old = signal.getsignal(signal.SIGWINCH)
        output = Terminal()
        output.write("previous command history\n")
        progress = self.progress(output)
        with self.assertRaises(RuntimeError):
            with progress.watch_resize():
                progress.begin(0, Phase.CHECK)
                progress.finish(Status.CACHED)
                progress.begin(1, Phase.PREPARE)
                raise RuntimeError("synthetic failure")
        self.assertIs(signal.getsignal(signal.SIGWINCH), old)
        screen = primary_output(output.getvalue())
        self.assertIn("previous command history", screen)
        self.assertEqual(screen.count("[01/02]"), 1)
        self.assertNotIn("RUNNING", screen)
        self.assertNotIn("\x1b[?1049", output.getvalue())

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
