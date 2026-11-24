#!/usr/bin/env python3
"""Phase 6A: trusted, sequential development bootstrap orchestration.

initialize: confirmation-free guarded development provisioning.
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

if __package__:
    from .bootstrap_progress import Phase, Progress, Status, emit_result
    from .bootstrap_process import Cancellation, ChildRunner, InitializationCancelled, UnexpectedApproval
else:
    from bootstrap_progress import Phase, Progress, Status, emit_result
    from bootstrap_process import Cancellation, ChildRunner, InitializationCancelled, UnexpectedApproval

ROOT = Path(__file__).resolve().parents[2]
BOOTSTRAP = ROOT / "scripts/bootstrap.sh"
STATE = Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state"))) / "transcendence/dev"
LOCK = STATE / "unified-startup.lock"

# Deliberately sequenced: NO parallel execution and NO giant privileged runner.
STAGES = (
    ("host", "--check-host", "--prepare-host"),
    ("resources", "--check-resources", "--prepare-resources"),
    ("vault", "--check-vault", "--prepare-vault"),
    ("vault-ui", "--check-vault-ui-optional", None),
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
    ("prometheus", "--check-prometheus", "--prepare-prometheus"),
    ("grafana-secrets", "--check-grafana-secrets", "--prepare-grafana-secrets"),
    ("grafana-agent", "--check-grafana-agent", "--prepare-grafana-agent"),
    ("grafana", "--check-grafana", "--prepare-grafana"),
    ("grafana-https", "--check-grafana-https", None),
)
STAGE_BY_NAME = {name: (check, prepare) for name, check, prepare in STAGES}
RESUMABLE = (
    "postgres", "backend-agent", "backend", "support-services",
    "frontend", "nginx", "prometheus", "grafana-agent", "grafana",
)
READ_ONLY_ATTEMPTS = 3
READ_ONLY_RETRY_DELAY = 1
RECOVERY_AGENT_WAIT = 60
RECOVERY_AGENT_POLL = 2



# Exact, reviewed equivalents of bootstrap.sh's read-only dispatch.
# Host and optional Vault UI checks retain their shell entry points.
DIRECT_READ_ONLY_CHECKS = {
    "--check-resources": "bootstrap_resources.py",
    "--check-vault": "bootstrap_vault.py",
    "--check-vault-config": "bootstrap_vault_config.py",
    "--check-vault-secrets": "bootstrap_vault_secrets.py",
    "--check-postgres": "bootstrap_postgres.py",
    "--check-postgres-roles": "bootstrap_postgres_roles.py",
    "--check-migrations": "bootstrap_migrations.py",
    "--check-backend-agent": "bootstrap_backend_agent.py",
    "--check-backend": "bootstrap_backend.py",
    "--check-support-services": "bootstrap_support_services.py",
    "--check-frontend": "bootstrap_frontend.py",
    "--check-nginx": "bootstrap_nginx.py",
    "--check-prometheus": "bootstrap_prometheus.py",
    "--check-grafana-secrets": "bootstrap_grafana_secrets.py",
    "--check-grafana-agent": "bootstrap_grafana_agent.py",
    "--check-grafana": "bootstrap_grafana.py",
    "--check-grafana-https": "bootstrap_grafana_https.py",
}


def preflight_once():
    """Run standard shell/platform/dependency guards before direct probes."""
    try:
        result = subprocess.run(
            [str(BOOTSTRAP), "--orchestrator-preflight"],
            cwd=ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise UnifiedStartupError(
            "Cannot complete trusted orchestrator preflight"
        ) from None
    if result.returncode:
        raise UnifiedStartupError(
            "Orchestrator prerequisites failed; inspect bootstrap preflight"
        )


class SessionRefreshRequired(Exception):
    """Docker group enrollment succeeded; a fresh login is required."""


class UnifiedStartupError(Exception):
    """Public diagnostic intentionally contains no subprocess output or secrets."""


class RunningNotReady(UnifiedStartupError):
    """Identity was verified, but the routine readiness probe failed."""

    def __init__(self, name, targets):
        super().__init__(f"{name} is already running but unhealthy; manual review required")
        self.targets = targets


def command(flag, *, quiet=False, runner=None, lock_fd=None):
    """Invoke only fixed, reviewed bootstrap flags; no user-controlled argv."""
    if flag is None or flag not in {
        item
        for _, check, prepare in STAGES
        for item in (check, prepare)
        if item is not None
    }:
        raise UnifiedStartupError("Refusing unrecognized bootstrap operation")
    kwargs = {"cwd":ROOT, "check":False}
    if quiet:
        kwargs.update(stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if flag in DIRECT_READ_ONLY_CHECKS:
        script = ROOT / "scripts/dev" / DIRECT_READ_ONLY_CHECKS[flag]
        argv = [sys.executable, str(script),
                "check-ready" if runner is not None and flag == "--check-grafana-https" else "check"]
        tls = Path(os.environ.get(
            "XDG_STATE_HOME", str(Path.home() / ".local/state")
        )) / "transcendence/dev/tls"
        kwargs["env"] = {
            **os.environ,
            "FT_DEV_TLS_DIR": str(tls),
        }
    else:
        argv = [str(BOOTSTRAP), flag]
    if runner is not None:
        preparing = flag.startswith("--prepare-")
        if preparing:
            if lock_fd is None:
                raise UnifiedStartupError("Initialization stage requires the orchestrator lock")
            argv = [str(BOOTSTRAP), "--initialize-stage", flag, str(lock_fd)]
        try:
            return runner.run(argv, cwd=ROOT, env=kwargs.get("env"),
                              pass_fds=(lock_fd,) if preparing else (),
                              foreground=flag == "--prepare-host",
                              critical=flag == "--prepare-vault",
                              probe=not preparing,
                              operation=("Prepare " if preparing else "Check ") +
                              flag.removeprefix("--prepare-" if preparing else "--check-")) == 0
        except OSError:
            raise UnifiedStartupError("Cannot execute trusted bootstrap operation") from None
    if lock_fd is not None:
        raise UnifiedStartupError("Unexpected initialization lock")
    try:
        result = subprocess.run(argv, **kwargs)
    except OSError:
        raise UnifiedStartupError(
            "Cannot execute trusted bootstrap operation"
        ) from None
    return result.returncode == 0


# Names are fixed by the reviewed Compose contracts.
# This inventory is a performance hint, never an identity check.
FIRST_RUN_CONTAINERS = {
    "vault": ("transcendence-vault-dev-vault-1",),
    "postgres": ("transcendence-app-dev-postgres-1",),
    "backend-agent": ("transcendence-app-dev-vault-backend-agent-1",),
    "backend": ("transcendence-app-dev-backend-1",),
    "support-services": (
        "transcendence-app-dev-docker-proxy-1",
        "transcendence-app-dev-collector-1",
        "transcendence-app-dev-graph-engine-1",
    ),
    "frontend": ("transcendence-app-dev-frontend-1",),
    "nginx": ("transcendence-app-dev-nginx-1",),
    "prometheus": ("transcendence-app-dev-prometheus-1",),
    "grafana-agent": ("transcendence-app-dev-vault-grafana-agent-1",),
    "grafana": ("transcendence-app-dev-grafana-1",),
}


def definitely_missing_container(name):
    """True only when Docker confirms a required container is absent.

    Unknown Docker state, failed inventory and unlisted stages retain
    the existing retry behavior. This never establishes trust.
    """
    expected = FIRST_RUN_CONTAINERS.get(name)
    if expected is None:
        return False

    try:
        result = subprocess.run(
            ["docker", "ps", "-a", "--format", "{{.Names}}"],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False

    if result.returncode != 0:
        return False

    try:
        existing = set(result.stdout.decode("utf-8").splitlines())
    except UnicodeError:
        return False

    return all(item not in existing for item in expected)


def checked(name, *, first_run=False, runner=None):
    """Retry read-only probes; optionally trace safe stage names and durations.

    FT_DEV_STAGE_TIMING=1 is diagnostic-only: it changes neither the number of
    checks nor any trust/identity decision. No commands, arguments or secrets
    are included in the optional report.
    """
    trace = os.environ.get("FT_DEV_STAGE_TIMING") == "1"
    for attempt in range(READ_ONLY_ATTEMPTS):
        started = time.monotonic() if trace else None
        if runner is None:
            result = command(STAGE_BY_NAME[name][0], quiet=True)
        else:
            result = command(STAGE_BY_NAME[name][0], quiet=True, runner=runner)
        if trace:
            elapsed = max(0.0, time.monotonic() - started)
            outcome = "PASS" if result else "FAIL"
            print(f"TIMING: stage={name} attempt={attempt + 1}/{READ_ONLY_ATTEMPTS} "
                  f"result={outcome} elapsed={elapsed:.2f}s", flush=True)
        if result:
            return True
        if runner is not None and name == "grafana-https":
            # The typed check-ready entry point owns its bounded retry budget.
            return False
        if first_run and attempt == 0 and definitely_missing_container(name):
            return False
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
    if name == "vault-ui":
        import bootstrap_vault_ui as ui
        targets = ((ui.NAME, ui.inspect(), ui.verify_identity),)
    elif name == "postgres":
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
    elif name == "prometheus":
        import bootstrap_prometheus as prometheus
        targets = ((prometheus.NAME, prometheus.container(), prometheus.verify_identity),)
    elif name == "grafana-agent":
        import bootstrap_grafana_agent as grafana_agent
        if grafana_agent.credential_inventory() != "present":
            raise UnifiedStartupError(
                "Grafana AppRole missing; manual review required"
            )
        targets = ((
            grafana_agent.NAME,
            grafana_agent.container(),
            grafana_agent.verify_identity,
        ),)
    elif name == "grafana":
        import bootstrap_grafana as grafana
        targets = ((
            grafana.NAME,
            grafana.container(),
            grafana.verify_identity,
        ),)
    else:
        raise UnifiedStartupError(f"Cannot resume stage {name}")

    verified = []
    for container_name, item, verify in targets:
        state = verify_stopped_container(item, verify, container_name)
        if not isinstance(item.get("Id"), str) or not item["Id"]:
            raise UnifiedStartupError(f"Missing trusted Docker ID: {container_name}")
        verified.append((container_name, item["Id"], verify, state))
    if all(state == "running" for _, _, _, state in verified):
        raise RunningNotReady(name, tuple(verified))
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


def wait_recovery_backend_agent(targets):
    """Only explicit reboot recovery may wait for an already-running agent.

    Do not restart, replace, provision, rotate or accept a failed security
    check. Pin the verified container ID throughout the bounded read-only
    wait, and require the full existing readiness check before continuing.
    """
    import bootstrap_backend_agent as agent

    if len(targets) != 1:
        raise UnifiedStartupError("Unexpected backend-agent recovery inventory")
    name, expected_id, verify, state = targets[0]
    if name != agent.NAME or state != "running":
        raise UnifiedStartupError("Unexpected backend-agent recovery target")

    def inspect_health():
        try:
            item = agent.inspect_agent()
            if item is None or item.get("Id") != expected_id:
                raise UnifiedStartupError("Backend-agent identity changed during recovery")
            if verify_stopped_container(item, verify, name) != "running":
                raise UnifiedStartupError("Backend-agent stopped during recovery")
            health = ((item.get("State") or {}).get("Health") or {}).get("Status")
            if health not in ("starting", "unhealthy", "healthy"):
                raise UnifiedStartupError("Unexpected backend-agent health state")
            return health
        except UnifiedStartupError:
            raise
        except Exception:
            raise UnifiedStartupError("Backend-agent recovery inspection failed; manual review required") from None

    deadline = time.monotonic() + RECOVERY_AGENT_WAIT
    # Bound polling even if a clock is mocked or a check returns immediately.
    attempts = RECOVERY_AGENT_WAIT // RECOVERY_AGENT_POLL + 1
    for attempt in range(attempts):
        health = inspect_health()
        if health == "healthy":
            if time.monotonic() > deadline:
                break
            require("backend-agent")
            if inspect_health() != "healthy":
                raise UnifiedStartupError("Backend-agent readiness changed during recovery")
            return
        remaining = deadline - time.monotonic()
        if remaining <= 0 or attempt + 1 == attempts:
            break
        if attempt == 0:
            print("WAIT: trusted backend-agent health after Vault unseal (up to 60s polling)", flush=True)
        time.sleep(min(RECOVERY_AGENT_POLL, remaining))
    raise UnifiedStartupError("Backend-agent health wait expired; inspect --check-backend-agent; no restart attempted")


def resume(name, *, recovery=False):
    if checked(name):
        print(f"KEEP: {name} ready", flush=True)
        return
    try:
        targets = guard_resume(name)
    except RunningNotReady as exc:
        if not recovery or name != "backend-agent":
            raise
        wait_recovery_backend_agent(exc.targets)
        return
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
    # Reuse the guarded, non-provisioning application recovery path.
    start(recovery=True)
    print("RESULT: REBOOT RECOVERY READY", flush=True)


def prepare_prerequisites(runner, progress):
    """System authentication may still be required; never feed sudo credentials."""
    installer = str(ROOT / "scripts/install-deps.sh")
    images = str(ROOT / "scripts/dev/bootstrap_images.py")
    progress.begin(0, Phase.CHECK)
    check_code = runner.run([installer, "--initialize-check"], cwd=ROOT, probe=True,
                            probe_guard_codes=(3,), operation="Check local prerequisites")
    cached = check_code == 0
    if check_code not in (0, 1):
        runner.report_failure()
        raise UnifiedStartupError("Local prerequisite guard refused; inspect ./scripts/install-deps.sh --initialize-check")
    if not cached:
        progress.phase(Phase.PREPARE)
        # Authenticate on the real controlling TTY; the installer then uses
        # sudo -n, so it can stream progress without touching password input.
        if runner.run(["sudo", "-v"], cwd=ROOT, foreground=True,
                      operation="Authenticate sudo"):
            raise UnifiedStartupError("Sudo authentication failed; no dependency installation started")
        code = runner.run([installer, "--initialize-dev"], cwd=ROOT, foreground=True,
                          pause_for_auth=False, operation="Install missing prerequisites")
        if code == 2 and runner.last_diagnostics.session_refresh:
            raise SessionRefreshRequired()
        if code:
            raise UnifiedStartupError("System prerequisites failed; inspect ./scripts/install-deps.sh --check")
    progress.phase(Phase.VERIFY)
    for argv, label in (([str(BOOTSTRAP), "--orchestrator-preflight"], "Verify platform and prerequisites"),
                        ([sys.executable, "-m", "scripts.dev.bootstrap_linux_ca", "preflight"], "Verify existing CA provenance")):
        if runner.run(argv, cwd=ROOT, operation=label):
            raise UnifiedStartupError("System prerequisite verification failed; inspect local host state")
    progress.finish(Status.CACHED if cached else Status.DONE)

    progress.begin(1, Phase.CHECK)
    cached = runner.run([sys.executable, images, "check"], cwd=ROOT, probe=True, operation="Check local image inventory") == 0
    if not cached:
        progress.phase(Phase.PREPARE)
        if runner.run([sys.executable, images, "initialize"], cwd=ROOT, operation="Prepare missing images"):
            raise UnifiedStartupError("Docker image preparation failed; inspect bootstrap_images.py check")
    progress.finish(Status.CACHED if cached else Status.DONE)


def initialize(lock_fd, *, cancellation=None):
    """Single fixed, sequential provisioning flow; no approval prompts."""
    cancellation = cancellation or Cancellation()
    progress = Progress(("System dependencies", "Docker images") + tuple(
        name for name, _, _ in STAGES))
    with progress.watch_resize():
        runner = ChildRunner(progress, cancellation)
        try:
            prepare_prerequisites(runner, progress)
            for index, (name, check, prepare) in enumerate(STAGES, 2):
                cancellation.check()
                progress.begin(index, None if name == "vault-ui" else Phase.CHECK)
                if name == "vault-ui":
                    progress.finish(Status.SKIPPED)
                    continue
                if checked(name, first_run=True, runner=runner):
                    progress.finish(Status.CACHED)
                    continue
                if prepare is None:
                    runner.report_failure()
                    raise UnifiedStartupError(f"{name} verification failed; inspect ./scripts/bootstrap.sh {check}")
                if name == "vault":
                    vault_seal_guard()
                cancellation.check()
                progress.phase(Phase.PREPARE)
                if os.environ.get("FT_DEV_STAGE_TIMING") == "1":
                    print(f"PREPARE: {name}", flush=True)
                if not command(prepare, runner=runner, lock_fd=lock_fd):
                    raise UnifiedStartupError(
                        f"{name} preparation failed (exit {runner.last_returncode}); "
                        f"inspect ./scripts/bootstrap.sh {check}"
                    )
                progress.phase(Phase.VERIFY)
                if not checked(name, runner=runner):
                    runner.report_failure()
                    raise UnifiedStartupError(
                        f"{name} verification failed (exit {runner.last_returncode}); "
                        f"inspect ./scripts/bootstrap.sh {check}"
                    )
                progress.finish(Status.DONE)
            cancellation.check()
        except SessionRefreshRequired:
            progress.finish(Status.ACTION_REQUIRED)
            progress.summary()
            raise
        except InitializationCancelled:
            if progress.active is not None:
                progress.finish(Status.CANCELLED)
                progress.summary()
            raise
        except (UnifiedStartupError, OSError, UnexpectedApproval):
            if progress.active is not None:
                progress.finish(Status.FAILED)
                progress.summary()
            raise
        progress.summary()
        emit_result("UNIFIED INITIALIZATION READY", Status.DONE)


def start(*, recovery=False):
    print("=== Vault-backed routine startup (no provisioning or migrations) ===", flush=True)
    require("host")
    require("resources")
    vault_seal_guard()
    # Report optional gateway drift without preventing core recovery after a
    # reboot: HAProxy has restart: no and its enabled marker may persist.
    # Full health remains fail-closed for unexpected UI lifecycle state.
    if not checked("vault-ui"):
        print("WARN: optional Vault UI state unhealthy; run make vault-ui-status", flush=True)
    # Nginx's check walks the full trusted dependency chain down to Vault and
    # PostgreSQL. Rechecking each dependency separately is expensive and can
    # saturate a small VM with redundant nested Docker/psql invocations.
    if (
        checked("nginx")
        and checked("prometheus")
        and checked("grafana")
        and checked("grafana-https")
    ):
        print("KEEP: complete Vault-backed stack ready", flush=True)
        print("RESULT: UNIFIED STARTUP READY", flush=True)
        return
    # Recover only existing trusted stopped containers, in dependency order.
    # A running but persistently unhealthy container still fails closed.
    for name, _, _ in STAGES[2:]:
        if name == "vault-ui":
            # Never provision, start or require the administrative gateway.
            # A warning above and strict make health report any UI failure.
            print("SKIP: optional vault-ui lifecycle", flush=True)
            continue
        if name == "vault":
            require(name)
        elif name in RESUMABLE:
            if recovery and name == "backend-agent":
                resume(name, recovery=True)
            else:
                resume(name)
        else:
            require(name)
    print("RESULT: UNIFIED STARTUP READY", flush=True)


def check():
    print("=== Unified read-only readiness check ===", flush=True)
    require("host")
    require("resources")
    # The administrative gateway may be explicitly disabled.
    require("vault-ui")
    # Nginx's read-only check covers the entire transitive service chain.
    require("nginx")
    require("prometheus")
    # Grafana check includes Vault credentials, Agent delivery,
    # provisioning and authentication.
    require("grafana")
    require("grafana-https")
    print("RESULT: UNIFIED STACK READY (read-only)", flush=True)



def health_report():
    """Safe, read-only full-stack readiness with failure localization.

    Fast path executes the existing transitive Nginx readiness probe once,
    just like --check-all. On failure, a separate diagnostic pass checks the
    trusted stages in dependency order, stopping at the first failing stage.
    The per-stage helpers retain their identity/ACL/HTTP checks; stdout and
    stderr remain suppressed to avoid accidentally displaying secrets.

    This command deliberately never starts, repairs, unseals, or provisions.
    """
    print("=== Vault-backed development health report (read-only) ===", flush=True)
    for name in ("host", "resources"):
        if not checked(name):
            print(f"FAIL: {name}; inspect {STAGE_BY_NAME[name][0]}", flush=True)
            raise UnifiedStartupError(f"Health report failed at {name}")
        print(f"PASS: {name}", flush=True)

    # The Nginx check verifies TLS, HTTPS routes, Vite and API, and traverses
    # the backend, support services, Vault Agent, migrations, PG roles, and
    # Vault configuration. Do not repeat this expensive chain on success.
    if checked("nginx"):
        print("PASS: end-to-end Nginx HTTPS, application and trusted dependencies", flush=True)
        if not checked("vault-ui"):
            print("FAIL: vault-ui; inspect --check-vault-ui-optional", flush=True)
            raise UnifiedStartupError("Health report failed at vault-ui")
        print("PASS: Vault UI expected lifecycle state and Docker isolation", flush=True)
        if not checked("prometheus"):
            print("FAIL: prometheus; inspect --check-prometheus", flush=True)
            raise UnifiedStartupError("Health report failed at prometheus")

        print("PASS: Prometheus readiness and trusted identity", flush=True)

        if not checked("grafana"):
            # Locate a failed Grafana dependency without provisioning.
            for dependency in ("grafana-secrets", "grafana-agent"):
                if not checked(dependency):
                    print(
                        f"FAIL: {dependency}; inspect "
                        f"{STAGE_BY_NAME[dependency][0]}",
                        flush=True,
                    )
                    raise UnifiedStartupError(
                        f"Health report failed at {dependency}"
                    )

            print("FAIL: grafana; inspect --check-grafana", flush=True)
            raise UnifiedStartupError(
                "Health report failed at grafana or its checked dependencies"
            )

        print("PASS: Grafana authentication and provisioning", flush=True)

        if not checked("grafana-https"):
            print(
                "FAIL: grafana-https; inspect --check-grafana-https",
                flush=True,
            )
            raise UnifiedStartupError(
                "Health report failed at grafana-https"
            )

        print("PASS: Grafana HTTPS and WAF protection", flush=True)
        print("RESULT: DEVELOPMENT HEALTHY (read-only)", flush=True)
        return

    print("WARN: end-to-end check failed; locating the first failing dependency", flush=True)
    for name, _, _ in STAGES[2:]:
        if not checked(name):
            print(f"FAIL: {name}; inspect {STAGE_BY_NAME[name][0]}", flush=True)
            raise UnifiedStartupError(
                f"Health report failed at {name} (or one of its checked dependencies)"
            )
        print(f"PASS: {name}", flush=True)

    # All independently repeated probes passed after initial end-to-end
    # failure. Do not call this healthy: that could hide intermittent faults.
    raise UnifiedStartupError(
        "Intermittent readiness failure: initial HTTPS check failed; "
        "all individual read-only checks later passed"
    )


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
        yield fd
    finally:
        os.close(fd)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("initialize", "start", "check", "recover", "health-report"))
    args = parser.parse_args(argv)
    try:
        if os.geteuid() == 0:
            raise UnifiedStartupError("Run as an ordinary user, not with sudo")
        if args.mode == "initialize":
            # Do not mutate Windows trust or a remote Docker daemon.
            import platform
            if platform.system() != "Linux" or "microsoft" in platform.release().lower():
                raise UnifiedStartupError("Initialization requires native Ubuntu Linux")
            if os.environ.get("DEV_DOMAIN", "transcendence.test") != "transcendence.test":
                raise UnifiedStartupError("Initialization requires the default development domain")
            with exclusive_orchestrator_lock() as lock_fd, Cancellation().handling() as cancellation:
                initialize(lock_fd, cancellation=cancellation)
            return 0
        preflight_once()
        if args.mode == "check":
            check()
        elif args.mode == "health-report":
            health_report()
        else:
            with exclusive_orchestrator_lock():
                if args.mode == "recover":
                    recover()
                else:
                    start()
        return 0
    except SessionRefreshRequired:
        emit_result("INITIALIZATION PAUSED (sign out and back in; rerun make initialize)",
                    Status.ACTION_REQUIRED, stream=sys.stderr)
        return 2
    except InitializationCancelled as exc:
        emit_result("INITIALIZATION CANCELLED (no automatic rollback; inspect existing state)",
                    Status.CANCELLED, stream=sys.stderr)
        return 128 + exc.signum
    except UnexpectedApproval:
        emit_result("UNIFIED BOOTSTRAP STOPPED (unexpected approval; no automatic rollback)",
                    Status.FAILED, stream=sys.stderr)
        return 1
    except OSError:
        print("ERROR: Local bootstrap operation failed; inspect the active stage", file=sys.stderr)
        return 1
    except UnifiedStartupError as exc:
        print(f"ERROR: {exc}", file=sys.stderr, flush=True)
        if args.mode == "initialize":
            emit_result("UNIFIED BOOTSTRAP STOPPED (no resources removed)", Status.FAILED, stream=sys.stderr)
        else:
            print("RESULT: UNIFIED BOOTSTRAP STOPPED (no resources removed)", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
