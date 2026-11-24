"""Defer cooperative termination while committing irreplaceable recovery data.

Use only around a bounded critical operation. Subprocesses inside the region
that must survive a process-group SIGINT must run in a separate session.
The caller must still wait for the protected process; SIGKILL and crashes are
not recoverable by this mechanism.
"""

from contextlib import contextmanager
import signal


class DeferredInterrupt(KeyboardInterrupt):
    def __init__(self, signum):
        self.signum = signum
        super().__init__("Interrupted after critical recovery operation")


@contextmanager
def defer_termination():
    """Record the first SIGINT/SIGTERM, then deliver it after a successful body.

    On failure the original exception wins: an uncertain persistence result
    must never be reported as a successful commit. Main-thread POSIX only.
    """
    pending = None
    previous = {}

    def remember(signum, _frame):
        nonlocal pending
        if pending is None:
            pending = signum

    try:
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous[signum] = signal.signal(signum, remember)
        yield
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)
    if pending is not None:
        raise DeferredInterrupt(pending)
