#!/usr/bin/env python3
"""Phase 6A: trusted, sequential development bootstrap orchestration.

initialize: explicit interactive first-time provisioning using established stages.
start: resume ONLY existing, verified containers after Vault is unsealed.
check: read-only validation of the entire already-running stack.
recover: resume a known Vault container, prompt for manual Shamir shares,
         then resume existing application containers without provisioning.

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
import time

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
READ_ONLY_ATTEMPTS = 3
READ_ONLY_RETRY_DELAY = 1


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
    """Retry only read-only probes; never retry provisioning or mutation."""
    for attempt in range(READ_ONLY_ATTEMPTS):
        if command(STAGE_BY_NAME[name][0], quiet=True):
            return True
        if attempt + 1 < READ_ONLY_ATTEMPTS:
            time.sleep(READ_ONLY_RETRY_DELAY)
    return False


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
    if state.get("Running") is True and state.get("Status") == "running":
        return "running"
    if state.get("Running") is False and state.get("Status") in ("created", "exited"):
        return "stopped"
    raise UnifiedStartupError(f"Unexpected {label} state; manual review required")


def guard_resume(name):
    """Validate every existing container before a single Docker start.

    No `up`, image builds, provisioning, unseal or migrations are allowed in
    routine recovery. Each target retains its exact inspected Docker ID.
    """
    if name == "postgres":
        import bootstrap_postgres as pg
        targets = ((pg.NAME, pg.exact_container(pg.NAME), pg.verify_identity),)
    elif name == "backend-agent":
        import bootstrap_backend_agent as agent
        if agent.credential_inventory() != "present":
            raise UnifiedStartupError("Backend AppRole missing; use --initialize after review")
        targets = ((agent.NAME, agent.inspect_agent(), agent.verify_identity),)
    elif name == "backend":
        import bootstrap_backend as backend
        targets = ((backend.NAME, backend.container(), backend.verify_identity),)
    elif name == "support-services":
        import bootstrap_support_services as support
        targets = tuple(
            (support.name(service), support.inspect(service),
             lambda item, service=service: support.verify_identity(service, item))
            for service in support.SERVICES
        )
    elif name == "frontend":
        import bootstrap_frontend as frontend
        targets = ((frontend.NAME, frontend.container(), frontend.verify_identity),)
    elif name == "nginx":
        import bootstrap_nginx as nginx
        targets = ((nginx.NAME, nginx.container(), nginx.verify_identity),)
    else:
        raise UnifiedStartupError(f"Cannot resume stage {name}")

    verified = []
    for container_name, item, verify in targets:
        state = verify_stopped_container(item, verify, container_name)
        if not isinstance(item.get("Id"), str) or not item["Id"]:
            raise UnifiedStartupError(f"Missing trusted Docker ID: {container_name}")
        verified.append((container_name, item["Id"], verify, state))
    if all(state == "running" for _, _, _, state in verified):
        raise UnifiedStartupError(f"{name} is already running but unhealthy; manual review required")
    return tuple(verified)


def start_existing_only(name, targets):
    """Resume verified containers under the existing migration/provision lock.

    Bootstrap stage `apply` recursively rechecks the entire dependency graph
    before each Docker start, increasing the risk of Docker CLI timeouts.
    In routine recovery, prior stages are already fully
    checked by the orchestrator, and post-start checks stay mandatory.
    """
    import bootstrap_postgres as pg
    try:
        with pg.LOCK.open("rb") as lock:
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise UnifiedStartupError(
                    "Another provisioning/migration workflow is running"
                ) from None
            # Prevalidate the *entire* set before starting anything.
            for container_name, container_id, verify, expected in targets:
                current = pg.exact_container(container_name)
                if current is None or current.get("Id") != container_id:
                    raise UnifiedStartupError(f"Container changed: {container_name}")
                actual = verify_stopped_container(current, verify, container_name)
                if actual != expected:
                    raise UnifiedStartupError(f"Container state changed: {container_name}")

            for container_name, container_id, verify, state in targets:
                if state == "running":
                    print(f"KEEP: {container_name}", flush=True)
                    continue
                try:
                    result = subprocess.run(
                        ["docker", "start", container_name], cwd=ROOT,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        check=False, timeout=90,
                    )
                except (OSError, subprocess.TimeoutExpired):
                    raise UnifiedStartupError(
                        f"Docker start interrupted for {container_name}; inspect manually"
                    ) from None
                if result.returncode != 0:
                    raise UnifiedStartupError(
                        f"Docker start failed for {container_name}; inspect manually"
                    )
                after = pg.exact_container(container_name)
                if after is None or after.get("Id") != container_id:
                    raise UnifiedStartupError(f"Container identity changed: {container_name}")
                verify_stopped_container(after, verify, container_name)
                if (after.get("State") or {}).get("Running") is not True:
                    raise UnifiedStartupError(f"Container did not start: {container_name}")
                print(f"START: {container_name}", flush=True)
    except pg.PostgresBootstrapError:
        raise UnifiedStartupError(f"Docker inspection failed during {name} recovery") from None
    except OSError:
        raise UnifiedStartupError(f"Cannot acquire startup/migration lock for {name}") from None


def resume(name):
    if checked(name):
        print(f"KEEP: {name} ready", flush=True)
        return
    try:
        targets = guard_resume(name)
    except UnifiedStartupError:
        raise
    except Exception:
        raise UnifiedStartupError(f"Cannot inspect trusted {name} containers") from None
    print(f"RESUME: {name} (existing containers only)", flush=True)
    start_existing_only(name, targets)
    # The full transitive readiness/identity check still runs after every
    # start, with the existing bounded retry budget for read-only probes.
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



def recovery_vault():
    """Resume only a known, initialized Vault; never create or initialize one."""
    import bootstrap_vault as vault

    try:
        before = vault.inspect_container()
        if before is None:
            raise UnifiedStartupError(
                "Vault container missing: recovery cannot provision a replacement"
            )
        vault.verify_identity(before)
        host = before.get("HostConfig") or {}
        if host.get("Privileged") is not False or host.get("PortBindings"):
            raise UnifiedStartupError("Unexpected Vault host privileges/ports")
        state = before.get("State") or {}
        if state.get("Running") is True and state.get("Status") == "running":
            print("KEEP: trusted Vault container running", flush=True)
        elif state.get("Running") is False and state.get("Status") in ("created", "exited"):
            # This only starts the existing container; no Compose up, no init.
            try:
                result = subprocess.run(
                    ["docker", "start", vault.NAME], cwd=ROOT,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    check=False, timeout=90,
                )
            except (OSError, subprocess.TimeoutExpired):
                raise UnifiedStartupError("Unable to start trusted Vault container") from None
            if result.returncode != 0:
                raise UnifiedStartupError("Trusted Vault container start failed")
            after = vault.inspect_container()
            if after is None or not before.get("Id") or before.get("Id") != after.get("Id"):
                raise UnifiedStartupError("Vault identity changed during recovery")
            vault.verify_identity(after)
            if (after.get("State") or {}).get("Running") is not True:
                raise UnifiedStartupError("Vault did not start successfully")
            print("START: existing trusted Vault container", flush=True)
        else:
            raise UnifiedStartupError("Unexpected Vault container state; manual review required")
        seal = vault.status(tries=30)
        vault.verify_topology(seal, require_ready=False)
        if seal.get("initialized") is not True:
            raise UnifiedStartupError("Vault not initialized; refusing recovery or reinitialization")
        return vault, seal
    except UnifiedStartupError:
        raise
    except Exception:
        raise UnifiedStartupError("Vault recovery inspection failed; manual review required") from None


def interactive_unseal(vault, state):
    """The Vault CLI, not this process, reads each share from a trusted TTY."""
    if not state["sealed"]:
        return
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise UnifiedStartupError(
            "Vault sealed: interactive TTY required. Run --recover from a terminal "
            "or unseal manually and use --start"
        )
    progress = state.get("progress")
    if type(progress) is not int or not 0 <= progress < 2:
        raise UnifiedStartupError("Unexpected Vault unseal progress; manual review required")
    print("Vault is sealed (development Shamir 2-of-3).", flush=True)
    print("Enter distinct unseal shares ONLY into the hidden Vault CLI prompts.", flush=True)
    print("Do not pass shares as arguments or paste them into this chat.", flush=True)
    while state["sealed"]:
        print(f"UNSEAL: requesting share {progress + 1} of 2", flush=True)
        # Never use shell=True, argv secrets, environment secrets or captured IO.
        # HashiCorp Vault prompts for a hidden key when none is supplied.
        try:
            result = subprocess.run(
                ["docker", "exec", "-it", vault.NAME, "vault", "operator", "unseal"],
                cwd=ROOT, check=False, timeout=180,
            )
        except (OSError, subprocess.TimeoutExpired):
            raise UnifiedStartupError("Interactive Vault unseal command interrupted") from None
        if result.returncode != 0:
            raise UnifiedStartupError("Vault unseal rejected/interrupted; no application recovery")
        try:
            state = vault.status(tries=10)
            vault.verify_topology(state, require_ready=False)
        except Exception:
            raise UnifiedStartupError("Vault unseal result uncertain; manual review required") from None
        if state.get("initialized") is not True:
            raise UnifiedStartupError("Vault initialization changed unexpectedly")
        if state.get("sealed"):
            next_progress = state.get("progress")
            if type(next_progress) is not int or next_progress != progress + 1:
                raise UnifiedStartupError("Vault unseal progress unexpected; stop safely")
            progress = next_progress
            if progress >= 2:
                raise UnifiedStartupError("Vault still sealed at key threshold; stop safely")
        elif progress != 1:
            raise UnifiedStartupError("Vault unsealed before expected threshold; manual review")
    print("PASS: Vault unsealed using operator-entered shares", flush=True)


def recover():
    """After reboot: only resume known resources and require manual Vault unseal."""
    print("=== Trusted reboot recovery (manual Shamir unseal) ===", flush=True)
    require("host")
    require("resources")
    vault, seal = recovery_vault()
    interactive_unseal(vault, seal)
    require("vault")
    # Reuse Phase 6A's guarded, non-provisioning application recovery path.
    start()
    print("RESULT: REBOOT RECOVERY READY", flush=True)


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
    require("host")
    require("resources")
    vault_seal_guard()
    # Nginx's check walks the full trusted dependency chain down to Vault and
    # PostgreSQL. Rechecking each dependency separately is expensive and can
    # saturate a small VM with redundant nested Docker/psql invocations.
    if checked("nginx"):
        print("KEEP: complete Vault-backed stack ready", flush=True)
        print("RESULT: UNIFIED STARTUP READY", flush=True)
        return
    # Recover only existing trusted stopped containers, in dependency order.
    # A running but persistently unhealthy container still fails closed.
    for name, _, _ in STAGES[2:]:
        if name == "vault":
            require(name)
        elif name in RESUMABLE:
            resume(name)
        else:
            require(name)
    print("RESULT: UNIFIED STARTUP READY", flush=True)


def check():
    print("=== Unified read-only readiness check ===", flush=True)
    require("host")
    require("resources")
    # Nginx's read-only check covers the entire transitive service chain.
    require("nginx")
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
    parser.add_argument("mode", choices=("initialize", "start", "check", "recover"))
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
                elif args.mode == "recover":
                    recover()
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
