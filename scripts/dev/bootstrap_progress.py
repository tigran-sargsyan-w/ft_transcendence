"""Presentation-only initialization renderer; no raw child output."""
from dataclasses import dataclass
from enum import Enum
import os
import re
import shutil
import signal
from contextlib import contextmanager
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
    total: float = 0.0


COMPLETE = (Status.CACHED, Status.DONE, Status.SKIPPED)
STOP = (Status.FAILED, Status.CANCELLED, Status.ACTION_REQUIRED)
COLORS = {
    Status.RUNNING: "36", Status.DONE: "32", Status.CACHED: "32",
    Status.SKIPPED: "90", Status.PENDING: "90", Status.ACTION_REQUIRED: "33",
    Status.FAILED: "31", Status.CANCELLED: "33",
    Status.MISSING: "33", Status.NOT_READY: "33", Status.UNCONFIRMED: "33",
}


def live_enabled(stream, env):
    # Only terminals with known cursor-control support use live rendering.
    term = env.get("TERM", "")
    return (stream.isatty() and sys.stderr.isatty()
            and term.startswith(("xterm", "screen", "tmux", "rxvt", "alacritty", "foot", "kitty", "wezterm"))
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
    """Inline progress; rebuild the transient preview after terminal resize.

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
        self._rows = []
        self._geometry = None
        self._report_width = None
        self._needs_report = False
        self._resized = False
        self._settle_until = float("-inf")
        self._last_refresh = float("-inf")
        self.steps = {}
        self.width = terminal_width or (lambda: self._size().columns)
        self.height = terminal_height or (lambda: self._size().lines)
        self.verbose = env.get("FT_DEV_BOOTSTRAP_VERBOSE") == "1"
        self.live = live_enabled(self.stream, env) and interactive is not False
        self._inline = self.live
        self._write("[+] Initializing ft_transcendence\n")

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

    @staticmethod
    def _shorten(label, size):
        if len(label) <= size:
            return label
        return label[:size-3] + "..." if size >= 4 else "." * max(0, size)

    @staticmethod
    def _layout(width):
        # One column for stage and nested statuses. Reserve a fixed phase
        # field; smaller terminals omit timing, then phase, not status.
        if width >= 70:
            return width-44, 18, "full"
        if width >= 48:
            return width-27, 18, "compact"
        return max(0, width-18), 16, "none"

    def _stage_parts(self, index, now):
        stage = self.stages[index]
        elapsed = (now-stage.started if stage.status is Status.RUNNING else stage.elapsed)
        total = now-self.started if stage.status is Status.RUNNING else stage.total
        phase = f" ({stage.phase.value})" if stage.phase else ""
        timing = ("" if stage.status is Status.PENDING else
                  f"{max(0.0, elapsed):.1f}s | total {max(0.0, total):.1f}s")
        prefix = f" => [{index+1:02d}/{len(self.stages):02d}] "
        return prefix, stage, phase, timing, elapsed

    def _line(self, index, width=None, now=None):
        now = self.clock() if now is None else now
        prefix, stage, phase, timing, elapsed = self._stage_parts(index, now)
        status = self._color(stage.status.value, stage.status)
        if not self.live:
            return prefix + stage.label + " " + status + phase + (" " + timing if timing else "")
        width = max(1, self.width()-1) if width is None else width
        column, field, mode = self._layout(width)
        available = column-len(prefix)-2
        if available < 1:
            return self._color(stage.status.value[:width], stage.status)
        label = self._shorten(stage.label, available)
        head = prefix + label.ljust(available) + "  " + status
        tail = phase if mode != "none" else ""
        if timing and mode != "none":
            value = timing if mode == "full" else f"{max(0.0, elapsed):.1f}s"
            time_width = width-column-field
            if len(value) <= time_width and len(stage.status.value+tail) <= field:
                return head + tail + " " * (field-len(stage.status.value+tail)) + value.rjust(time_width)
        return head + tail

    def _final_line(self, index, width):
        # Never shorten names or omit timings from durable output. At narrow
        # sizes use a consistent compact table and let normal terminal wrap.
        prefix, stage, phase, timing, _ = self._stage_parts(index, self.clock())
        column = max(self._layout(width)[0],
                     len(prefix)+max(len(item.label) for item in self.stages)+2)
        status = self._color(stage.status.value, stage.status)
        return (prefix + stage.label.ljust(column-len(prefix)-2) + "  " + status
                + " " * (18-len(stage.status.value)) + timing.rjust(max(25, width-column-18)))

    def _plain_transition(self):
        if self.active is not None:
            prefix, stage, phase, timing, _ = self._stage_parts(self.active, self.clock())
            self._write(prefix + stage.label + " " + self._color(stage.status.value, stage.status)
                        + phase + (" " + timing if timing else "") + "\n")

    def _size_key(self):
        return max(1, self.width()-1), max(1, self.height())

    def _detach_preview(self):
        """Leave the invalidated preview behind, not the inline renderer.

        Never seek backwards through reflowed rows. Start a fresh transient
        region after the resize burst settles. A stale preview can remain in
        scrollback, but stage transitions must not become a verbose log.
        """
        if self._rows:
            # Explicit CR+LF, not LF alone: some emulators retain a nonzero
            # cursor column when the old progress bar reflows.
            self._write("\r\n")
        self._rows = []
        self._geometry = None
        self._needs_report = True
        self._resized = False
        self._settle_until = self.clock() + .3

    def _check_geometry(self, geometry):
        if not self._inline:
            return False
        if self._rows and (self._resized or geometry != self._geometry):
            self._detach_preview()
            return False
        if self._resized:
            self._resized = False
            self._settle_until = self.clock() + .3
        return True

    @contextmanager
    def watch_resize(self):
        """The signal only invalidates the viewport; no terminal IO in a handler."""
        previous = None
        if self.live:
            def resized(_signum, _frame):
                self._resized = True
            previous = signal.signal(signal.SIGWINCH, resized)
        try:
            yield
        finally:
            try:
                self.close()
            finally:
                if previous is not None:
                    signal.signal(signal.SIGWINCH, previous)

    def _paint(self, rows, geometry, suffix=""):
        """Diff the bounded region; cursor rests at column zero of its last row.

        Write one frame at a time. Newlines grow the region, existing rows
        use cursor movement. Unchanged rows are never erased or rewritten.
        """
        if not self._check_geometry(self._size_key()):
            if suffix:
                self._write(suffix)
            return
        if geometry != self._size_key() or self._resized:
            self._detach_preview()
            if suffix:
                self._write(suffix)
            return
        old = self._rows
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
        # Check again immediately before emitting relative movement. SIGWINCH
        # may still race a terminal write; there is no atomic resize-and-write
        # primitive. Never reuse the old anchor once a resize is observed.
        if self._resized or self._size_key() != geometry:
            self._detach_preview()
            if suffix:
                self._write(suffix)
            return
        self._rows = list(rows)
        self._geometry = geometry if rows else None
        self._write("".join(chunks))

    def _erase(self, suffix=""):
        if self._rows:
            self._paint([], self._size_key(), suffix)
        elif suffix:
            self._write(suffix)

    def _notice_line(self, message, width=None):
        if width is not None:
            message = message[:width]
        prefix, sep, detail = message.partition(":")
        status = (Status.FAILED if prefix == "ERROR" else
                  Status.ACTION_REQUIRED if prefix in ("SECURITY", "WARNING", "ACTION REQUIRED") else
                  Status.CANCELLED if prefix == "CANCELLED" else None)
        return self._color(prefix, status) + sep + detail if status else message

    def close(self):
        """Clear only a still-trusted preview; leave prior commands untouched."""
        self._erase()

    def _step_line(self, label, status, width=None):
        prefix = "    => => "
        if not self.live:
            return prefix + label + "  " + status.value
        width = max(1, self.width()-1) if width is None else width
        column, _, _ = self._layout(width)
        available = column-len(prefix)-2
        if available >= 1:
            return (prefix + self._shorten(label, available).ljust(available)
                    + "  " + self._color(status.value, status))
        return self._color(status.value[:width], status)

    def _bar(self, width, now):
        completed = sum(stage.status in COMPLETE for stage in self.stages)
        tail = f" {completed}/{len(self.stages)} | elapsed {max(0.0, now-self.started):.1f}s"
        size = max(0, width-len(tail)-2)
        filled = size * completed // len(self.stages)
        if width < len(tail)+2:
            return tail.strip()[:width]
        return "[" + self._color("="*filled, Status.DONE) + "-"*(size-filled) + "]" + tail

    def refresh(self, *, force=False):
        if not self._inline or self.paused or self.active is None:
            return
        geometry = self._size_key()
        if not self._check_geometry(geometry):
            return
        now = self.clock()
        # At impractically small dimensions, omit only the transient preview.
        # Durable stage results remain visible; expanding the window restores
        # normal rendering. Do not switch to plain phase-transition logging.
        if geometry[0] < 33 or geometry[1] < 4:
            return
        if now < self._settle_until:
            return
        if not force and self._rows and now-self._last_refresh < .2:
            return
        self._last_refresh = now
        width, height = geometry
        budget = max(1, height-3)
        rows = [self._line(self.active, width, now)]
        if budget > 1:
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
        self._paint(rows, geometry)

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
        if self._inline:
            self.refresh(force=True)
        elif phase is not None:
            self._plain_transition()

    def phase(self, phase):
        if self.active is None or not isinstance(phase, Phase):
            raise ValueError("Expected active stage and reviewed phase")
        stage = self.stages[self.active]
        if stage.phase is not phase:
            stage.phase = phase
            self.steps.clear()
            if self._inline:
                self.refresh(force=True)
            else:
                self._plain_transition()

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

    def pause(self):
        if self.live:
            self.close()
            self._write("    Authentication may be required (sudo)\n")
        self.paused = True

    def resume(self):
        self.paused = False
        self.refresh(force=True)

    def notice(self, message):
        # Notices are durable and immediately visible, including during
        # authentication, failure and cancellation. Never erase them later.
        self._erase(self._notice_line(message) + "\n")
        self.refresh(force=True)

    def finish(self, status):
        if self.active is None or status not in COMPLETE + STOP:
            raise ValueError("Expected active stage and terminal status")
        index = self.active
        stage = self.stages[index]
        now = self.clock()
        stage.elapsed = max(0.0, now - stage.started)
        stage.total = max(0.0, now - self.started)
        stage.status = status
        stage.phase = None
        if self.live:
            width = self._size_key()[0]
            if self._report_width is not None and self._report_width != width:
                self._needs_report = True
            self._report_width = width
            self._erase(self._final_line(index, width) + "\n")
        else:
            self._write(self._line(index) + "\n")
        self.active = None
        self.steps.clear()
        self.next_index += 1
        self.stopped = status in STOP
        if self.stopped or self.next_index == len(self.stages):
            self.close()

    def summary(self):
        if self.active is not None or (not self.stopped and self.next_index != len(self.stages)):
            raise ValueError("Initialization is still in progress")
        self.close()
        if self.live:
            width = self._size_key()[0]
            if self._needs_report or (self._report_width is not None and width != self._report_width):
                self._write("\n[+] Final stage report (actual results; previous progress above)\n")
                self._write("".join(self._final_line(i, width) + "\n" for i in range(self.next_index)))
        completed = sum(stage.status in COMPLETE for stage in self.stages)
        elapsed = max(0.0, self.clock() - self.started)
        self._write(f"[+] Finished {completed}/{len(self.stages)} stages in {elapsed:.1f}s\n")
