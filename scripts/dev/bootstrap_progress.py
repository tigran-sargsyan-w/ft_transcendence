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
    # Only terminals with known alternate-screen support use live rendering.
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
    """Isolate the live viewport from permanent terminal history.

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
        self._history = []
        self._printed = 0
        self._screen = False
        self._geometry = None
        self._resized = False
        self._last_refresh = float("-inf")
        self.steps = {}
        self.width = terminal_width or (lambda: self._size().columns)
        self.height = terminal_height or (lambda: self._size().lines)
        self.verbose = env.get("FT_DEV_BOOTSTRAP_VERBOSE") == "1"
        self.live = live_enabled(self.stream, env) and interactive is not False
        self._header = "[+] Initializing ft_transcendence"
        self._write(self._header + "\n")

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
        total = now-self.started if stage.status is Status.RUNNING else stage.total
        phase = f" ({stage.phase.value})" if stage.phase else ""
        prefix = f" => [{index + 1:02d}/{len(self.stages):02d}] "
        status = stage.status.value
        timing = ("" if stage.status is Status.PENDING else
                  f"{max(0.0, elapsed):.1f}s | total {max(0.0, total):.1f}s")
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
            if previous is not None:
                signal.signal(signal.SIGWINCH, previous)
            self.close()

    def _paint(self, rows, geometry):
        """Absolute coordinates in an isolated screen, never in scrollback.

        A resize can reflow rows between the size read and this write. Only
        the disposable alternate screen is affected; SIGWINCH invalidates
        the next frame even if the window has returned to its old size.
        """
        reset = not self._screen or self._geometry != geometry or self._resized
        self._resized = False
        chunks = []
        if not self._screen:
            chunks.append("\x1b[?1049h\x1b[?25l")
            self._screen = True
        old = self._rows
        if reset:
            chunks.append("\x1b[2J\x1b[H")
            old = []
        height = geometry[1]
        for i in range(min(height, max(len(old), len(rows)))):
            before = old[i] if i < len(old) else None
            after = rows[i] if i < len(rows) else None
            if before == after:
                continue
            chunks.append(f"\x1b[{i+1};1H")
            if after is None:
                chunks.append("\x1b[2K")
            else:
                chunks.extend((after, "\x1b[K"))
        self._rows = list(rows)
        self._geometry = geometry
        if chunks:
            self._write("".join(chunks))

    def _notice_line(self, message, width=None):
        if width is not None:
            message = message[:width]
        prefix, sep, detail = message.partition(":")
        status = (Status.FAILED if prefix == "ERROR" else
                  Status.ACTION_REQUIRED if prefix in ("SECURITY", "WARNING", "ACTION REQUIRED") else
                  Status.CANCELLED if prefix == "CANCELLED" else None)
        return self._color(prefix, status) + sep + detail if status else message

    def _history_line(self, entry, width=None):
        if isinstance(entry, int):
            # Permanent output must retain the complete stage name and timing,
            # even in a narrow terminal (ordinary wrapping is safe there).
            if width is None:
                width = max(1, self.width()-1)
                stage = self.stages[entry]
                if width < len(stage.label) + 58:
                    prefix = f" => [{entry+1:02d}/{len(self.stages):02d}] "
                    return (prefix + stage.label + " " + self._color(stage.status.value, stage.status)
                            + f" {stage.elapsed:.1f}s | total {stage.total:.1f}s")
            return self._line(entry, width)
        return self._notice_line(entry, width)

    def close(self):
        """Restore the original screen, then append each durable line once.

        No cursor movement or erase is performed in the primary screen.
        Also called before foreground authentication and on exceptional exits.
        """
        if self._screen:
            self._screen = False
            self._rows = []
            self._geometry = None
            self._write("\x1b[?25h\x1b[?1049l")
        if self._printed < len(self._history):
            lines = self._history[self._printed:]
            self._write("".join(self._history_line(entry) + "\n" for entry in lines))
            self._printed = len(self._history)

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
        completed = sum(stage.status in COMPLETE for stage in self.stages)
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
        width = max(1, self.width()-1)
        height = max(1, self.height())
        geometry = (width, height)
        if (not force and not self._resized and geometry == self._geometry
                and now-self._last_refresh < .2):
            return
        self._last_refresh = now

        # The current operation always has a row. Reserve room for the bar,
        # recent committed stages and at least a few pending/substep rows.
        header = [self._header[:width]] if height >= 4 else []
        budget = height-len(header)
        rows = [self._line(self.active, width, now)]
        if budget >= 2:
            pending = list(range(self.active+1, len(self.stages)))
            # Reserve at most half of a short viewport for history; active
            # operations must remain visible even near the end of the run.
            reserve = min(len(self._history), max(0, budget-3),
                          max(budget//2, budget-2-min(6, len(self.steps))-1))
            remaining = max(0, budget-reserve-2)
            step_budget = min(6, remaining, len(self.steps))
            if step_budget:
                rows.extend(self._step_line(label, status, width)
                            for label, status in list(self.steps.items())[-step_budget:])
            remaining -= step_budget
            if len(pending) > remaining and remaining:
                rows.extend(self._line(i, width, now) for i in pending[:remaining-1])
                rows.append(f"    ... {len(pending)-remaining+1} more pending"[:width])
            else:
                rows.extend(self._line(i, width, now) for i in pending[:remaining])
            rows.append(self._bar(width, now))
        available = max(0, budget-len(rows))
        history = self._history[-available:] if available else []
        rows = header + [self._history_line(entry, width) for entry in history] + rows
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

    def pause(self):
        if self.live:
            self.close()
            self._write("    Authentication may be required (sudo)\n")
        self.paused = True

    def resume(self):
        self.paused = False
        self.refresh(force=True)

    def notice(self, message):
        if not self.live:
            self._write(message + "\n")
            return
        self._history.append(message)
        if self.paused or self.active is None:
            self.close()
        else:
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
            self._history.append(index)
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
        completed = sum(stage.status in COMPLETE for stage in self.stages)
        elapsed = max(0.0, self.clock() - self.started)
        self._write(f"[+] Finished {completed}/{len(self.stages)} stages in {elapsed:.1f}s\n")
