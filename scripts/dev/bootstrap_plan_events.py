"""Typed, opt-in plan observations; standalone helpers keep legacy output."""
from contextlib import contextmanager
import json

_sink = None
_primary = True
STATUSES = frozenset(("EXISTS", "MISSING", "READY", "UNVERIFIED",
                      "OPTIONAL", "NOT_READY", "ACTION_REQUIRED"))


@contextmanager
def nested():
    """Observations of existing nested prerequisite plans are not target results."""
    global _primary
    previous = _primary
    _primary = False
    try:
        yield
    finally:
        _primary = previous


def report(label, status, *, primary=True):
    if _sink is not None:
        if status not in STATUSES:
            raise ValueError("Unexpected plan observation")
        _sink.write(json.dumps([label, status, primary and _primary]) + "\n")
        _sink.flush()


def container(label, item, *, optional=False):
    if _sink is None:
        return
    if item is None:
        report(label, "OPTIONAL" if optional else "MISSING")
        return
    state = item.get("State")
    state = state if isinstance(state, dict) else {}
    status = state.get("Status")
    health_state = state.get("Health")
    health = health_state.get("Status") if isinstance(health_state, dict) else None
    # Never interpolate arbitrary Docker fields into the presentation.
    if status in ("created", "running", "paused", "restarting", "removing",
                  "exited", "dead"):
        label += " (" + status + ")"
    report(label, "EXISTS")
    if health in ("healthy", "unhealthy", "starting"):
        report("Docker health reported " + health + " (readiness not checked)",
               "UNVERIFIED", primary=False)
