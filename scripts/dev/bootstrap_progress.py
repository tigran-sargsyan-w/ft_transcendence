"""Shared bootstrap progress presentation; no raw child output."""
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
    MISSING = "MISSING"
    NOT_READY = "NOT READY"
    UNCONFIRMED = "UNCONFIRMED"
    CACHED = "CACHED"
    DONE = "DONE"
    SKIPPED = "SKIPPED"
    ACTION_REQUIRED = "ACTION REQUIRED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class ProgressProfile(Enum):
    FULL = "full"
    COMPACT = "compact"


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
    Status.MISSING: "33", Status.NOT_READY: "33", Status.UNCONFIRMED: "33",
}


def live_enabled(stream, env):
    return (stream.isatty() and sys.stderr.isatty()
            and env.get("TERM", "") not in ("", "dumb")
            and not env.get("CI") and "NO_COLOR" not in env
            and env.get("FT_DEV_BOOTSTRAP_VERBOSE") != "1"
            and env.get("FT_DEV_STAGE_TIMING") != "1")


def emit_result(message, status, *, stream=None, env=None):
    """Color only the result state; preserve the plain log contract."""
    stream = sys.stdout if stream is None else stream
    env = os.environ if env is None else env
    state, separator, detail = message.partition(" (")
    if live_enabled(sys.stdout, env) and stream.isatty():
        state = f"\x1b[{COLORS[status]}m{state}\x1b[0m"
    print("RESULT: " + state + separator + detail, file=stream, flush=True)


class Progress:
    """Commit final rows once, repaint only a bounded transient region.

    All strings from callers must be reviewed static labels, never raw child
    output. Plain mode is append-only; verbose keeps reviewed substeps.
    """

    def __init__(self, labels, *, stream=None, clock=time.monotonic, env=None,
                 terminal_width=None, terminal_height=None, interactive=None,
                 header="Initializing ft_transcendence", profile=ProgressProfile.FULL,
                 allow_not_ready=False):
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
        self._rows = []
        self._last_refresh = float("-inf")
        self.steps = {}
        self.width = terminal_width or (lambda: self._size().columns)
        self.height = terminal_height or (lambda: self._size().lines)
        self.verbose = env.get("FT_DEV_BOOTSTRAP_VERBOSE") == "1"
        self.live = live_enabled(self.stream, env) and interactive is not False
        if not isinstance(profile, ProgressProfile):
            raise ValueError("Expected a reviewed progress profile")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 _-]{0,119}", header):
            raise ValueError("Expected a static command header")
        self.profile = profile
        # Only routine startup may record a nonfatal readiness result before
        # entering its existing guarded resume path. Initialization stays strict.
        self.completed_statuses = COMPLETE + ((Status.NOT_READY,) if allow_not_ready else ())
        header = "[+] " + header
        if self.live:
            header = header[:max(1, self.width()-1)]
        self._write(header + "\n")

    @property
    def drawn(self):
        return len(self._rows)

    def _size(self):
        try:
            return os.get_terminal_size(self.stream.fileno())
        except (OSError, ValueError, AttributeError):
            return shutil.get_terminal_size((80, 24))

    def _write(self, value):
        self.stream.write(value)
        self.stream.flush()

    def _color(self, value, status):
        return f"\x1b[{COLORS[status]}m{value}\x1b[0m" if self.live else value

    def _line(self, index, width=None, now=None):
        stage = self.stages[index]
        now = self.clock() if now is None else now
        elapsed = (now - stage.started if stage.status is Status.RUNNING else stage.elapsed)
        phase = f" ({stage.phase.value})" if stage.phase else ""
        prefix = f" => [{index + 1:02d}/{len(self.stages):02d}] "
        status = stage.status.value
        timing = ("" if stage.status is Status.PENDING else
                  f"{max(0.0, elapsed):.1f}s | total {max(0.0, now-self.started):.1f}s")
        if not self.live:
            return prefix + stage.label + " " + status + phase + (" " + timing if timing else "")
        width = max(1, self.width()-1) if width is None else width
        # Shared columns across live rows. Leave the rightmost cell unused
        # to prevent autowrap. Narrow windows progressively drop timing detail.
        state_width = 18
        time_width = max(25, len(timing))
        label_width = width - len(prefix) - state_width - time_width - 3
        if label_width >= 8:
            head = (prefix + stage.label[:label_width].ljust(label_width) + "  " +
                    self._color(status, stage.status) + phase)
            return (head + " " * (state_width-len(status+phase)+1) +
                    timing.rjust(time_width)) if timing else head
        compact_time = f" {max(0.0, elapsed):.1f}s" if timing else ""
        for tail in (phase + compact_time, phase, ""):
            label_width = width - len(prefix) - len(status+tail) - 1
            if label_width >= 1:
                return (prefix + stage.label[:label_width].ljust(label_width) + " " +
                        self._color(status, stage.status) + tail)
        return self._color(status[:width], stage.status)

    def _paint(self, rows, suffix=""):
        """Diff the bounded region; cursor rests at column zero of its last row.

        Write one frame at a time. Newlines grow the region, existing rows
        use cursor movement. Unchanged rows are never erased or rewritten.
        """
        old = self._rows
        height = max(1, self.height())
        if len(old) > height:
            old = old[-height:]
        if old == rows and not suffix:
            return
        cursor = max(0, len(old)-1)
        extent = len(old)
        chunks = []

        def move(target):
            nonlocal cursor, extent
            chunks.append("\r")
            if cursor > target:
                chunks.append(f"\x1b[{cursor-target}A")
                cursor = target
            elif cursor < target:
                existing = min(target-cursor, max(0, extent-1-cursor))
                if existing:
                    chunks.append(f"\x1b[{existing}B")
                    cursor += existing
                if cursor < target:
                    chunks.append("\n" * (target-cursor))
                    cursor = target
                    extent = max(extent, cursor+1)

        for i in range(max(len(old), len(rows))):
            before = old[i] if i < len(old) else None
            after = rows[i] if i < len(rows) else None
            if before == after:
                continue
            move(i)
            if after is None:
                chunks.append("\x1b[2K")
            else:
                # Overwrite in place without blanking the row. Erase only
                # the remainder, after the new text is already present.
                chunks.extend((after, "\x1b[K"))
            chunks.append("\r")
        move(max(0, len(rows)-1))
        chunks.append(suffix)
        self._rows = list(rows)
        self._write("".join(chunks))

    def _erase(self, suffix=""):
        if self._rows:
            self._paint([], suffix)
        elif suffix:
            self._write(suffix)

    def _step_line(self, label, status, width=None):
        prefix = "    => => "
        if not self.live:
            return prefix + label + "  " + status.value
        width = max(1, self.width()-1) if width is None else width
        column = width - 25 - 18 - 1
        if column >= len(prefix)+8 and len(prefix)+len(label) <= column:
            return prefix + label.ljust(column-len(prefix)) + self._color(status.value, status)
        available = width-len(prefix)-len(status.value)-1
        if available >= 1:
            return prefix + label[:available].ljust(available) + " " + self._color(status.value, status)
        return self._color(status.value[:width], status)

    def _bar(self, width, now):
        completed = sum(stage.status in self.completed_statuses for stage in self.stages)
        tail = f" {completed}/{len(self.stages)} | elapsed {max(0.0, now-self.started):.1f}s"
        size = max(0, width-len(tail)-2)
        filled = size * completed // len(self.stages)
        if width < len(tail)+2:
            return tail.strip()[:width]
        return "[" + self._color("="*filled, Status.DONE) + "-"*(size-filled) + "]" + tail

    def refresh(self, *, force=False):
        if not self.live or self.paused or self.active is None:
            return
        now = self.clock()
        if not force and now-self._last_refresh < .2:
            return
        self._last_refresh = now
        width = max(1, self.width()-1)
        height = max(1, self.height())
        budget = max(1, height-3)
        rows = [self._line(self.active, width, now)]
        if budget > 1 and self.profile is ProgressProfile.COMPACT:
            step_budget = min(6, budget-1)
            recent = list(self.steps.items())[-step_budget:] if step_budget else []
            rows.extend(self._step_line(label, status, width) for label, status in recent)
        elif budget > 1:
            step_budget = min(6, max(0, budget-2))
            recent = list(self.steps.items())[-step_budget:] if step_budget else []
            rows.extend(self._step_line(label, status, width) for label, status in recent)
            pending = list(range(self.active+1, len(self.stages)))
            remaining = max(0, budget-len(rows)-1)
            if len(pending) > remaining and remaining:
                rows.extend(self._line(i, width, now) for i in pending[:remaining-1])
                rows.append(f"    ... {len(pending)-remaining+1} more pending"[:width])
            else:
                rows.extend(self._line(i, width, now) for i in pending[:remaining])
            rows.append(self._bar(width, now))
        self._paint(rows)

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
            self.refresh(force=True)
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
                self.refresh(force=True)
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
        # Preserve row order for status-only updates; a changed status must
        # not move otherwise unchanged operations around the screen.
        self.steps[label] = status
        if self.verbose:
            self._write(self._step_line(label, status) + "\n")

    def pause(self, message="Authentication may be required (sudo)"):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 .:/(),_+-]{0,119}", message):
            raise ValueError("Expected a static pause message")
        if self.live:
            self._erase()
            line = "    " + message
            self._write(line[:max(1, self.width()-1)] + "\n")
        self.paused = True

    def resume(self):
        self.paused = False
        self.refresh(force=True)

    def notice(self, message):
        # A persistent notice owns its own row; clear the preview and commit
        # the notice before drawing a new preview below it.
        prefix, sep, detail = message.partition(":")
        status = (Status.FAILED if prefix in ("ERROR", "FAIL") else
                  Status.ACTION_REQUIRED if prefix in ("SECURITY", "WARNING", "WARN", "WAIT", "ACTION REQUIRED") else
                  Status.CANCELLED if prefix == "CANCELLED" else None)
        line = self._color(prefix, status) + sep + detail if status else message
        self._erase(line + "\n")
        self.refresh(force=True)

    def finish(self, status):
        if self.active is None or status not in self.completed_statuses + STOP:
            raise ValueError("Expected active stage and terminal status")
        index = self.active
        stage = self.stages[index]
        stage.elapsed = max(0.0, self.clock() - stage.started)
        stage.status = status
        stage.phase = None
        self._erase(self._line(index) + "\n")
        self.active = None
        self.steps.clear()
        self.next_index += 1
        self.stopped = status in STOP

    def summary(self):
        if self.active is not None or (not self.stopped and self.next_index != len(self.stages)):
            raise ValueError("Initialization is still in progress")
        completed = sum(stage.status in self.completed_statuses for stage in self.stages)
        elapsed = max(0.0, self.clock() - self.started)
        self._write(f"[+] Finished {completed}/{len(self.stages)} stages in {elapsed:.1f}s\n")
