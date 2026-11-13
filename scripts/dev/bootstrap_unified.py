#!/usr/bin/env python3
"""Phase 6A: trusted, sequential development bootstrap orchestration.

initialize: explicit interactive first-time provisioning using established stages.
start: resume ONLY existing, verified containers after Vault is unsealed.
check: read-only validation of the entire already-running stack.

Each established stage owns its existing migration/provisioning lock. This
orchestrator uses a DIFFERENT lock; sharing the stage lock would self-deadlock.
No root tokens, DSNs, SecretIDs, or unseal keys are handled by this module.
"""

import argparse
from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import stat
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
BOOTSTRAP = ROOT / "scripts/bootstrap.sh"
STATE = Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state"))) / "transcendence/dev"
LOCK = STATE / "unified-startup.lock"

# Deliberately sequenced: NO parallel execution and NO giant privileged runner.
STAGES = (
    ("host", "--check-host", "--prepare-host"),
    ("resources", "--check-resources", "--prepare-resources"),
    ("vault", "--check-vault", "--prepare-vault"),
    ("vault-config", "--check-vault-config", "--prepare-vault-config"),
    ("vault-secrets", "--check-vault-secrets", "--prepare-vault-secrets"),
    ("postgres", "--check-postgres", "--prepare-postgres"),
    ("postgres-roles", "--check-postgres-roles", "--prepare-postgres-roles"),
    ("migrations", "--check-migrations", "--prepare-migrations"),
    ("backend-agent", "--check-backend-agent", "--prepare-backend-agent"),
    ("backend", "--check-backend", "--prepare-backend"),
    ("support-services", "--check-support-services", "--prepare-support-services"),
    ("frontend", "--check-frontend", "--prepare-frontend"),
    ("nginx", "--check-nginx", "--prepare-nginx"),
)
STAGE_BY_NAME = {name: (check, prepare) for name, check, prepare in STAGES}
RESUMABLE = ("postgres", "backend-agent", "backend", "support-services", "frontend", "nginx")


class UnifiedStartupError(Exception):
    """Public diagnostic intentionally contains no subprocess output or secrets."""


def command(flag, *, quiet=False):
    """Invoke only fixed, reviewed bootstrap flags; no user-controlled argv."""
    if flag not in {item for _, check, prepare in STAGES for item in (check, prepare)}:
        raise UnifiedStartupError("Refusing unrecognized bootstrap operation")
    kwargs = {"cwd":ROOT, "check":False}
    if quiet:
        kwargs.update(stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        result = subprocess.run([str(BOOTSTRAP), flag], **kwargs)
    except OSError:
        raise UnifiedStartupError("Cannot execute trusted bootstrap.sh") from None
    return result.returncode == 0


def checked(name):
    return command(STAGE_BY_NAME[name][0], quiet=True)


def require(name):
    if not checked(name):
        raise UnifiedStartupError(
            f"{name} not ready; inspect its --check-* output before proceeding"
        )
    print(f"PASS: {name}", flush=True)


def verify_stopped_container(item, verify, label):
    if item is None:
        raise UnifiedStartupError(f"Missing {label}; use --initialize after review")
    try:
        verify(item)
    except Exception:
        raise UnifiedStartupError(f"Untrusted {label} container; manual review required") from None
    state = item.get("State") or {}
    if state.get("Running") is True:
        return "running"
    if state.get("Status") in ("created", "exited"):
        return "stopped"
    raise UnifiedStartupError(f"Unexpected {label} state; manual review required")


def guard_resume(name):
    """Never use an apply helper unless expected containers already exist.

    This prevents routine startup from silently provisioning a new PostgreSQL
    cluster, issuing a SecretID, or creating replacement Compose containers.
    """
    if name == "postgres":
        import bootstrap_postgres as pg
        states = (verify_stopped_container(pg.exact_container(pg.NAME), pg.verify_identity, name),)
    elif name == "backend-agent":
        import bootstrap_backend_agent as agent
        if agent.credential_inventory() != "present":
            raise UnifiedStartupError("Backend AppRole missing; use --initialize after review")
        states = (verify_stopped_container(agent.inspect_agent(), agent.verify_identity, name),)
    elif name == "backend":
        import bootstrap_backend as backend
        states = (verify_stopped_container(backend.container(), backend.verify_identity, name),)
    elif name == "support-services":
        import bootstrap_support_services as support
        states = tuple(verify_stopped_container(
            support.inspect(service),
            lambda item, service=service: support.verify_identity(service, item),
            service,
        ) for service in support.SERVICES)
    elif name == "frontend":
        import bootstrap_frontend as frontend
        states = (verify_stopped_container(frontend.container(), frontend.verify_identity, name),)
    elif name == "nginx":
        import bootstrap_nginx as nginx
        states = (verify_stopped_container(nginx.container(), nginx.verify_identity, name),)
    else:
        raise UnifiedStartupError(f"Cannot resume stage {name}")
    if all(state == "running" for state in states):
        raise UnifiedStartupError(f"{name} is already running but unhealthy; manual review required")
    return states


def resume(name):
    if checked(name):
        print(f"KEEP: {name} ready", flush=True)
        return
    guard_resume(name)
    print(f"RESUME: {name} (existing containers only)", flush=True)
    # Invoke reviewed per-stage apply directly: its own flock, identity and
    # prerequisite checks remain active. The top-level prepare flag would
    # prompt and is reserved for the interactive --initialize path.
    script = ROOT / "scripts/dev" / ("bootstrap_" + name.replace("-", "_") + ".py")
    try:
        result = subprocess.run([sys.executable, str(script), "apply"], cwd=ROOT, check=False)
    except OSError:
        raise UnifiedStartupError(f"Cannot invoke {name} resume helper") from None
    if result.returncode != 0:
        raise UnifiedStartupError(f"{name} resume failed; no cleanup attempted")
    require(name)


def vault_seal_guard():
    """Do not turn an initialized but sealed Vault into an auto-unseal shortcut."""
    import bootstrap_vault as vault
    item = vault.inspect_container()
    if item is None or not (item.get("State") or {}).get("Running"):
        return
    try:
        vault.verify_identity(item)
        state = vault.status(tries=1)
        vault.verify_topology(state, require_ready=False)
    except Exception:
        raise UnifiedStartupError("Vault identity/readiness uncertain; manual review") from None
    if state["initialized"] and state["sealed"]:
        raise UnifiedStartupError(
            "Vault is sealed. Unseal manually using the existing trusted procedure; "
            "never supply unseal keys to this orchestrator"
        )


def initialize():
    if not sys.stdin.isatty():
        raise UnifiedStartupError("--initialize requires an interactive terminal")
    print("=== Trusted first-time bootstrap (individual confirmations required) ===", flush=True)
    for name, check, prepare in STAGES:
        if checked(name):
            print(f"KEEP: {name} ready", flush=True)
            continue
        if name == "vault":
            vault_seal_guard()
        print(f"PREPARE: {name}", flush=True)
        if not command(prepare):
            raise UnifiedStartupError(f"{name} preparation failed or was declined; stopped safely")
        require(name)
    print("RESULT: UNIFIED INITIALIZATION READY", flush=True)


def start():
    print("=== Vault-backed routine startup (no provisioning or migrations) ===", flush=True)
    for name, _, _ in STAGES:
        if name == "vault":
            vault_seal_guard()
        if name in RESUMABLE:
            resume(name)
        else:
            require(name)
    print("RESULT: UNIFIED STARTUP READY", flush=True)


def check():
    print("=== Unified read-only readiness check ===", flush=True)
    for name, _, _ in STAGES:
        require(name)
    print("RESULT: UNIFIED STACK READY (read-only)", flush=True)


@contextmanager
def exclusive_orchestrator_lock():
    # Separate lock from infra/vault/scripts/run-migrations.sh: child stages
    # acquire that lock themselves, so locking it here would self-deadlock.
    STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    if STATE.is_symlink() or not STATE.is_dir() or STATE.stat().st_uid != os.getuid():
        raise UnifiedStartupError("Unsafe unified startup state directory")
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(LOCK, flags, 0o600)
    except OSError:
        raise UnifiedStartupError("Cannot safely open unified startup lock") from None
    try:
        lock_stat = os.fstat(fd)
        if (not stat.S_ISREG(lock_stat.st_mode) or lock_stat.st_uid != os.getuid()
                or lock_stat.st_mode & 0o077):
            raise UnifiedStartupError("Unsafe unified startup lock permissions")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise UnifiedStartupError("Another unified bootstrap is already running") from None
        yield
    finally:
        os.close(fd)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("initialize", "start", "check"))
    args = parser.parse_args(argv)
    try:
        if os.geteuid() == 0:
            raise UnifiedStartupError("Run as an ordinary user, not with sudo")
        if args.mode == "check":
            check()
        else:
            with exclusive_orchestrator_lock():
                if args.mode == "initialize":
                    initialize()
                else:
                    start()
        return 0
    except UnifiedStartupError as exc:
        print(f"ERROR: {exc}", file=sys.stderr, flush=True)
        print("RESULT: UNIFIED BOOTSTRAP STOPPED (no resources removed)",
              file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
