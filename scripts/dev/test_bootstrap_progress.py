"""Offline tests for initialization progress presentation."""
import io
import unittest
from unittest.mock import patch

from bootstrap_progress import Phase, Progress, ProgressProfile, Status, emit_result


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

    def test_full_profile_preserves_default_rendering(self):
        default, explicit = Terminal(), Terminal()
        a = self.progress(default)
        b = Progress(("Vault", "PostgreSQL"), stream=explicit,
                     clock=lambda: self.now, env={"TERM": "xterm"},
                     terminal_width=lambda: 80, terminal_height=lambda: 10,
                     header="Initializing ft_transcendence", profile=ProgressProfile.FULL)
        for progress in (a, b):
            progress.begin(0, Phase.CHECK)
            progress.substep("Verify identity", Status.DONE)
            progress.refresh(force=True)
            progress.pause()
            progress.resume()
            progress.finish(Status.CACHED)
            progress.begin(1)
            progress.finish(Status.SKIPPED)
            progress.summary()
        self.assertEqual(default.getvalue(), explicit.getvalue())

    def test_compact_profile_keeps_nested_events_without_pending_or_bar(self):
        output = Terminal()
        progress = Progress(("Gateway", "HTTPS"), stream=output,
                            clock=lambda: self.now, env={"TERM": "xterm"},
                            terminal_width=lambda: 100, terminal_height=lambda: 10,
                            header="Enabling Vault UI", profile=ProgressProfile.COMPACT)
        progress.begin(0, Phase.CHECK)
        progress.substep("Verify identity", Status.DONE)
        self.now = 1.0
        progress.refresh(force=True)
        self.assertIn("Enabling Vault UI", output.getvalue())
        self.assertIn("Verify identity", output.getvalue())
        self.assertNotIn("PENDING", output.getvalue())
        self.assertNotIn("| elapsed", output.getvalue())
        progress.pause("Manual Shamir unseal")
        self.assertIn("Manual Shamir unseal", output.getvalue())
        self.assertEqual(progress.drawn, 0)
        progress.resume()
        progress.finish(Status.DONE)
        progress.begin(1)
        progress.finish(Status.DONE)
        progress.summary()
        self.assertIn("Finished 2/2 stages", output.getvalue())

    def test_nonfatal_not_ready_is_explicitly_opt_in(self):
        ordinary = self.progress(io.StringIO())
        ordinary.begin(0)
        with self.assertRaises(ValueError):
            ordinary.finish(Status.NOT_READY)
        ordinary.finish(Status.FAILED)
        output = io.StringIO()
        progress = Progress(("Check stack", "Resume stack"), stream=output,
                            allow_not_ready=True)
        progress.begin(0)
        progress.finish(Status.NOT_READY)
        progress.begin(1)
        progress.finish(Status.DONE)
        progress.summary()
        self.assertIn("Check stack NOT READY", output.getvalue())
        self.assertIn("Finished 2/2 stages", output.getvalue())

    def test_profile_and_header_reject_unreviewed_controls(self):
        for kwargs in ({"profile": "compact"}, {"header": "Bad\x1b[2J"},
                       {"header": "Bad\nheader"}):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValueError):
                    Progress(("Vault",), stream=io.StringIO(), **kwargs)
        progress = self.progress(Terminal())
        with self.assertRaises(ValueError):
            progress.pause("Bad\nmessage")

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
        # Minimal terminal interpretation of the renderer's exact CSI set.
        rows, row, col = [""], 0, 0
        for token in re.findall(r"\x1b\[[0-9;]*[A-Za-z]|[\s\S]", output.getvalue()):
            if re.fullmatch(r"\x1b\[\d+A", token):
                row = max(0, row - int(token[2:-1]))
            elif re.fullmatch(r"\x1b\[\d+B", token):
                row += int(token[2:-1])
                while row >= len(rows):
                    rows.append("")
            elif token == "\x1b[2K":
                rows[row] = ""
            elif token == "\x1b[K":
                rows[row] = rows[row][:col]
            elif token.startswith("\x1b"):
                continue
            elif token == "\r":
                col = 0
            elif token == "\n":
                row += 1
                col = 0
                if row == len(rows):
                    rows.append("")
            else:
                line = rows[row].ljust(col)
                rows[row] = line[:col] + token + line[col+1:]
                col += 1
        screen = "\n".join(rows)
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

        # A bounded viewport with scrollback. Cursor-up must not edit rows
        # that have scrolled off screen; newline may extend the scrollback.
        rows = ["" for _ in range(height)]
        row = col = top = 0
        for token in re.findall(r"\x1b\[[0-9;]*[A-Za-z]|[\s\S]", output.getvalue()):
            if re.fullmatch(r"\x1b\[\d+A", token):
                row = max(top, row-int(token[2:-1]))
            elif re.fullmatch(r"\x1b\[\d+B", token):
                row = min(top+height-1, row+int(token[2:-1]))
            elif token == "\x1b[2K":
                rows[row] = ""
            elif token == "\x1b[K":
                rows[row] = rows[row][:col]
            elif token.startswith("\x1b"):
                continue
            elif token == "\r":
                col = 0
            elif token == "\n":
                row += 1
                col = 0
                if row >= top+height:
                    top += 1
                    rows.append("")
            else:
                self.assertLess(col, width-1, "unexpected wrap")
                line = rows[row].ljust(col)
                rows[row] = line[:col]+token+line[col+1:]
                col += 1
        screen = "\n".join(rows)
        for i in range(1, 18):
            self.assertEqual(screen.count(f"[{i:02d}/21]"), 1)
        self.assertNotIn("[18/21]", screen)
        self.assertNotIn("PENDING", screen)
        self.assertNotIn("RUNNING", screen)
        self.assertNotIn("Verify operation", screen)
        self.assertEqual(screen.count("SECURITY: Preserve recovery material."), 1)
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
        self.assertTrue(all(len(strip(row)) <= 64 for row in progress._rows))

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
