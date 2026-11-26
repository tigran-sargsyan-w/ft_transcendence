"""Presentation scope for routine bootstrap commands.

Only the renderer's timer runs on a helper thread. All checks, lifecycle
decisions, signals, subprocesses and operator input stay on the calling
thread, using the original guarded functions. No child output is parsed.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from threading import Event, RLock, Thread

if __package__:
    from .bootstrap_progress import Phase, Progress, Status, emit_result
else:
    from bootstrap_progress import Phase, Progress, Status, emit_result


_current = ContextVar("bootstrap_cli_presentation", default=None)


@dataclass
class Outcome:
    status: Status = Status.DONE


class Presentation:
    def __init__(self, labels, header, *, allow_not_ready=False, **kwargs):
        self.progress = Progress(labels, header=header,
                                 allow_not_ready=allow_not_ready, **kwargs)
        self.lock = RLock()
        self.done = Event()
        self.ticker = None
        self.results = []

    def start(self):
        if self.progress.live:
            self.ticker = Thread(target=self._tick, name="bootstrap-progress", daemon=True)
            self.ticker.start()

    def _tick(self):
        # Event.wait makes shutdown immediate; no sleep is added to operations.
        while not self.done.wait(.2):
            with self.lock:
                try:
                    self.progress.refresh()
                except (OSError, ValueError):
                    # A failed terminal write must not trigger lifecycle work.
                    # The next foreground write retains its normal error behavior.
                    return

    def close(self):
        self.done.set()
        if self.ticker is not None and self.ticker.ident is not None:
            self.ticker.join()

    @contextmanager
    def stage(self, label, phase, outcome):
        with self.lock:
            index = self.progress.next_index
            if index >= len(self.progress.stages) or self.progress.stages[index].label != label:
                raise ValueError("Unexpected bootstrap presentation stage")
            self.progress.begin(index, phase)
        try:
            yield outcome
        except BaseException as exc:
            with self.lock:
                cancelled = isinstance(exc, KeyboardInterrupt)
                for label, status in tuple(self.progress.steps.items()):
                    if status is Status.RUNNING:
                        self.progress.substep(label, Status.CANCELLED if cancelled else Status.UNCONFIRMED)
                self.progress.finish(Status.CANCELLED if cancelled else Status.FAILED)
            raise
        else:
            with self.lock:
                self.progress.finish(outcome.status)

    def substep(self, label, status):
        with self.lock:
            self.progress.substep(label, status)
            self.progress.refresh(force=True)

    def notice(self, message):
        with self.lock:
            self.progress.notice(message)

    @contextmanager
    def interactive(self, message):
        with self.lock:
            self.progress.pause(message)
        # No renderer writes or captured input while the CLI owns the TTY.
        try:
            yield
        except BaseException:
            # A failed/cancelled prompt leaves the preview erased. The outer
            # stage commits its terminal failure row without resuming repaint.
            raise
        else:
            with self.lock:
                self.progress.resume()


@contextmanager
def presentation(labels, header, **kwargs):
    session = Presentation(labels, header, **kwargs)
    token = _current.set(session)
    try:
        session.start()
        yield session
    except BaseException:
        session.close()
        if session.progress.stopped:
            session.progress.summary()
        raise
    else:
        session.close()
        session.progress.summary()
        for message in session.results:
            emit_result(message, Status.DONE, stream=session.progress.stream)
    finally:
        session.close()
        _current.reset(token)


@contextmanager
def stage(label, phase=Phase.CHECK, *, success=Status.DONE):
    outcome = Outcome(success)
    current = _current.get()
    if current is None:
        yield outcome
    else:
        with current.stage(label, phase, outcome):
            yield outcome


def substep(label, status):
    current = _current.get()
    if current is not None:
        current.substep(label, status)


@contextmanager
def step(label, *, success=Status.DONE):
    outcome = Outcome(success)
    substep(label, Status.RUNNING)
    try:
        yield outcome
    except BaseException as exc:
        substep(label, Status.CANCELLED if isinstance(exc, KeyboardInterrupt) else Status.FAILED)
        raise
    else:
        substep(label, outcome.status)


def legacy(message):
    """Suppress redundant, reviewed legacy progress when a renderer owns it."""
    current = _current.get()
    if current is None:
        print(message, flush=True)
    elif current.progress.verbose:
        current.notice(message)


def notice(message):
    current = _current.get()
    if current is None:
        print(message, flush=True)
    else:
        current.notice(message)


def result(message):
    current = _current.get()
    if current is None:
        print("RESULT: " + message, flush=True)
    else:
        current.results.append(message)


@contextmanager
def interactive(message):
    current = _current.get()
    if current is None:
        yield
    else:
        with current.interactive(message):
            yield
