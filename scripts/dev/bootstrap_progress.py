"""Presentation-only progress for sequential development initialization.

The caller owns provisioning, child processes and readiness decisions. Only
reviewed stage labels and structured events belong here, never child output.
"""

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


class Progress:
    """Append completed rows; repaint only the active row on a capable TTY.

    Call refresh periodically while a child runs. Plain mode emits only state
    transitions. No threads, signal handlers, raw logs or subprocesses here.
    """

    def __init__(self, labels, *, stream=None, clock=time.monotonic, env=None,
                 terminal_width=None, interactive=None):
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
        self.rendered = False
        self.width = terminal_width or (lambda: shutil.get_terminal_size((80, 24)).columns)
        tty = self.stream.isatty() and sys.stderr.isatty()
        if interactive is not None:
            tty = tty and interactive
        self.live = (tty and env.get("TERM", "") not in ("", "dumb")
                     and not env.get("CI")
                     and env.get("FT_DEV_BOOTSTRAP_VERBOSE") != "1"
                     and env.get("FT_DEV_STAGE_TIMING") != "1")
        self._write("[+] Initializing ft_transcendence\n")

    def _write(self, value):
        self.stream.write(value)
        self.stream.flush()

    def _line(self, index):
        stage = self.stages[index]
        elapsed = (self.clock() - stage.started
                   if stage.status is Status.RUNNING else stage.elapsed)
        phase = f" ({stage.phase.value})" if stage.phase else ""
        total = max(0.0, self.clock() - self.started)
        prefix = f" => [{index + 1:02d}/{len(self.stages):02d}] "
        suffix = f" {stage.status.value}{phase} {max(0.0, elapsed):.1f}s | total {total:.1f}s"
        if not self.live:
            return prefix + stage.label + suffix
        width = max(1, self.width() - 1)
        available = width - len(prefix) - len(suffix)
        if available < 1:
            return (prefix + stage.label + suffix)[:width]
        label = stage.label[:available]
        return prefix + label.ljust(available, ".") + suffix

    def _render(self, index, *, final=False):
        line = self._line(index)
        if self.live:
            prefix = "\r\x1b[2K" if self.rendered else ""
            self._write(prefix + line + ("\n" if final else ""))
            self.rendered = not final
        else:
            self._write(line + "\n")

    def begin(self, index):
        if self.stopped or self.active is not None or index != self.next_index or index >= len(self.stages):
            raise ValueError("Stages must run once, in order")
        self.active = index
        stage = self.stages[index]
        stage.status = Status.RUNNING
        stage.started = self.clock()
        self._render(index)

    def phase(self, phase):
        if self.active is None or not isinstance(phase, Phase):
            raise ValueError("Expected active stage and reviewed phase")
        stage = self.stages[self.active]
        if stage.phase is not phase:
            stage.phase = phase
            self._render(self.active)

    def refresh(self):
        if self.live and self.active is not None:
            self._render(self.active)

    def finish(self, status):
        if self.active is None or status not in (Status.CACHED, Status.DONE,
                Status.SKIPPED, Status.FAILED, Status.CANCELLED):
            raise ValueError("Expected active stage and terminal status")
        index = self.active
        stage = self.stages[index]
        stage.elapsed = max(0.0, self.clock() - stage.started)
        stage.status = status
        stage.phase = None
        self.active = None
        self.next_index += 1
        self.stopped = status in (Status.FAILED, Status.CANCELLED)
        self._render(index, final=True)

    def summary(self):
        if self.active is not None or (not self.stopped and self.next_index != len(self.stages)):
            raise ValueError("Initialization is still in progress")
        completed = sum(stage.status in (Status.CACHED, Status.DONE, Status.SKIPPED)
                        for stage in self.stages)
        elapsed = max(0.0, self.clock() - self.started)
        self._write(f"[+] Finished {completed}/{len(self.stages)} stages in {elapsed:.1f}s\n")
