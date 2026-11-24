"""Presentation-only initialization renderer; no raw child output."""
from dataclasses import dataclass
from enum import Enum
import os
import re
import shutil
import sys
import time


class Status(Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    CACHED = "CACHED"
    DONE = "DONE"
    SKIPPED = "SKIPPED"
    ACTION_REQUIRED = "ACTION REQUIRED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class Phase(Enum):
    CHECK = "check"
    PREPARE = "prepare"
    VERIFY = "verify"


@dataclass
class Stage:
    label: str
    status: Status = Status.PENDING
    phase: Phase | None = None
    started: float | None = None
    elapsed: float = 0.0


COMPLETE = (Status.CACHED, Status.DONE, Status.SKIPPED)
STOP = (Status.FAILED, Status.CANCELLED, Status.ACTION_REQUIRED)
COLORS = {
    Status.RUNNING: "36", Status.DONE: "32", Status.CACHED: "32",
    Status.SKIPPED: "90", Status.PENDING: "90", Status.ACTION_REQUIRED: "33",
    Status.FAILED: "31", Status.CANCELLED: "33",
}


class Progress:
    """Commit final rows once, repaint only a bounded transient region.

    All strings from callers must be reviewed static labels, never raw child
    output. Plain mode is append-only; verbose keeps reviewed substeps.
    """

    def __init__(self, labels, *, stream=None, clock=time.monotonic, env=None,
                 terminal_width=None, terminal_height=None, interactive=None):
        self.stream = sys.stdout if stream is None else stream
        self.clock = clock
        env = os.environ if env is None else env
        labels = tuple(labels)
        if not labels or any(not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 -]*", label)
                             for label in labels) or len(set(labels)) != len(labels):
            raise ValueError("Expected unique, static stage labels")
        self.stages = [Stage(label) for label in labels]
        self.started = clock()
        self.active = None
        self.next_index = 0
        self.stopped = False
        self.paused = False
        self.drawn = 0
        self.steps = {}
        self.width = terminal_width or (lambda: shutil.get_terminal_size((80, 24)).columns)
        self.height = terminal_height or (lambda: shutil.get_terminal_size((80, 24)).lines)
        self.verbose = env.get("FT_DEV_BOOTSTRAP_VERBOSE") == "1"
        tty = self.stream.isatty() and sys.stderr.isatty()
        if interactive is not None:
            tty = tty and interactive
        self.live = (tty and env.get("TERM", "") not in ("", "dumb")
                     and not env.get("CI") and "NO_COLOR" not in env
                     and not self.verbose and env.get("FT_DEV_STAGE_TIMING") != "1")
        header = "[+] Initializing ft_transcendence"
        if self.live:
            header = header[:max(1, self.width()-1)]
        self._write(self._color(header, Status.RUNNING) + "\n")

    def _write(self, value):
        self.stream.write(value)
        self.stream.flush()

    def _color(self, value, status):
        return f"\x1b[{COLORS[status]}m{value}\x1b[0m" if self.live else value

    def _line(self, index):
        stage = self.stages[index]
        elapsed = (self.clock() - stage.started
                   if stage.status is Status.RUNNING else stage.elapsed)
        phase = f" ({stage.phase.value})" if stage.phase else ""
        prefix = f" => [{index + 1:02d}/{len(self.stages):02d}] "
        if stage.status is Status.PENDING:
            suffix = "PENDING"
        else:
            total = max(0.0, self.clock() - self.started)
            suffix = f"{stage.status.value}{phase} {max(0.0, elapsed):.1f}s | total {total:.1f}s"
        if not self.live:
            return prefix + stage.label + " " + suffix
        width = max(1, self.width() - 1)
        # Do not fill an entire wide terminal with dots. On narrow terminals
        # retain the status before optional timing fields.
        column = min(30, max(1, width - len(prefix) - len(suffix) - 2))
        line = prefix + stage.label[:column].ljust(column) + "  " + suffix
        if len(line) > width:
            short = stage.status.value
            if len(prefix) + len(short) + 1 < width:
                available = width - len(prefix) - len(short) - 1
                line = prefix + stage.label[:available].ljust(available) + " " + short
            else:
                line = (prefix + short)[:width]
        return self._color(line, stage.status)

    def _erase(self):
        if self.drawn:
            # Cursor is on the final transient row, not below it.
            self._write("\r\x1b[2K" + "\x1b[1A\r\x1b[2K" * (self.drawn - 1))
            self.drawn = 0

    def _step_line(self, label, status):
        line = "    => => " + label + "  " + status.value
        if self.live:
            width = max(1, self.width() - 1)
            if len(line) > width:
                suffix = " " + status.value
                line = (line[:max(0, width - len(suffix))] + suffix)[:width]
        return self._color(line, status)

    def _bar(self):
        completed = sum(stage.status in COMPLETE for stage in self.stages)
        elapsed = max(0.0, self.clock() - self.started)
        size = min(20, max(0, self.width() - 35))
        filled = size * completed // len(self.stages)
        line = f"[{'=' * filled}{'-' * (size - filled)}] {completed}/{len(self.stages)} | elapsed {elapsed:.1f}s"
        return self._color(line[:max(1, self.width()-1)], Status.RUNNING)

    def refresh(self):
        if not self.live or self.paused or self.active is None:
            return
        self._erase()
        # Bound rows to viewport; terminal resizing may reduce the preview.
        budget = max(1, self.height() - 3)
        rows = [self._line(self.active)]
        if budget > 1:
            step_budget = min(6, max(0, budget - 2))
            recent = list(self.steps.items())[-step_budget:] if step_budget else []
            rows.extend(self._step_line(label, status) for label, status in recent)
            pending = list(range(self.active + 1, len(self.stages)))
            remaining = max(0, budget - len(rows) - 1)
            if len(pending) > remaining and remaining:
                rows.extend(self._line(i) for i in pending[:remaining-1])
                rows.append(self._color(
                    f"    ... {len(pending)-remaining+1} more pending"[:max(1,self.width()-1)],
                    Status.PENDING))
            else:
                rows.extend(self._line(i) for i in pending[:remaining])
            rows.append(self._bar())
        self._write("\n".join(rows))
        self.drawn = len(rows)

    def begin(self, index, phase=None):
        if self.stopped or self.active is not None or index != self.next_index or index >= len(self.stages):
            raise ValueError("Stages must run once, in order")
        if phase is not None and not isinstance(phase, Phase):
            raise ValueError("Expected reviewed phase")
        self.active = index
        self.steps = {}
        stage = self.stages[index]
        stage.status = Status.RUNNING
        stage.started = self.clock()
        stage.phase = phase
        if self.live:
            self.refresh()
        elif phase is not None:
            self._write(self._line(index) + "\n")

    def phase(self, phase):
        if self.active is None or not isinstance(phase, Phase):
            raise ValueError("Expected active stage and reviewed phase")
        stage = self.stages[self.active]
        if stage.phase is not phase:
            stage.phase = phase
            self.steps.clear()
            if self.live:
                self.refresh()
            else:
                self._write(self._line(self.active) + "\n")

    def substep(self, label, status):
        if self.active is None:
            return
        if not isinstance(status, Status) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 .:/(),_+-]{0,119}", label):
            raise ValueError("Expected reviewed substep label and status")
        if self.steps.get(label) is status:
            return
        if len(self.steps) >= 128 and label not in self.steps:
            self.steps.pop(next(iter(self.steps)))
        self.steps[label] = status
        if self.live:
            self.refresh()
        elif self.verbose:
            self._write(self._step_line(label, status) + "\n")

    def pause(self):
        if self.live:
            self._erase()
            self._write("    => => Host authentication may be required (sudo)\n")
        self.paused = True

    def resume(self):
        self.paused = False
        self.refresh()

    def notice(self, message):
        if self.live:
            self._erase()
        status = (Status.FAILED if message.startswith("ERROR:") else
                  Status.ACTION_REQUIRED if message.startswith(("SECURITY:", "WARNING:", "ACTION REQUIRED:", "Cancellation")) else
                  Status.RUNNING)
        self._write(self._color(message, status) + "\n")
        self.refresh()

    def finish(self, status):
        if self.active is None or status not in COMPLETE + STOP:
            raise ValueError("Expected active stage and terminal status")
        index = self.active
        stage = self.stages[index]
        stage.elapsed = max(0.0, self.clock() - stage.started)
        stage.status = status
        stage.phase = None
        self._erase()
        self.active = None
        self.steps.clear()
        self.next_index += 1
        self.stopped = status in STOP
        self._write(self._line(index) + "\n")

    def summary(self):
        if self.active is not None or (not self.stopped and self.next_index != len(self.stages)):
            raise ValueError("Initialization is still in progress")
        completed = sum(stage.status in COMPLETE for stage in self.stages)
        elapsed = max(0.0, self.clock() - self.started)
        status = self.stages[self.next_index-1].status if self.stopped else Status.DONE
        self._write(self._color(f"[+] Finished {completed}/{len(self.stages)} stages in {elapsed:.1f}s", status) + "\n")
