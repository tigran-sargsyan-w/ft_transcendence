"""Typed, opt-in plan observations; standalone helpers keep legacy output."""
import json

_sink = None
STATUSES = frozenset(("EXISTS", "MISSING", "READY", "UNVERIFIED",
                      "OPTIONAL", "NOT_READY", "ACTION_REQUIRED"))


def report(label, status, *, primary=True):
    if _sink is not None:
        if status not in STATUSES:
            raise ValueError("Unexpected plan observation")
        _sink.write(json.dumps([label, status, primary]) + "\n")
        _sink.flush()


def container(label, item, *, optional=False):
    if item is None:
        report(label, "OPTIONAL" if optional else "MISSING")
        return
    state = item.get("State") or {}
    status = state.get("Status")
    health = (state.get("Health") or {}).get("Status")
    # Never interpolate arbitrary Docker fields into the presentation.
    if status in ("created", "running", "paused", "restarting", "removing",
                  "exited", "dead"):
        label += " (" + status + ")"
    report(label, "EXISTS")
    if health in ("healthy", "unhealthy", "starting"):
        report("Docker health " + health, "UNVERIFIED", primary=False)
