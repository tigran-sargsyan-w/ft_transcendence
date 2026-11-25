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
from contextlib import ExitStack, contextmanager
import fcntl
import os
from pathlib import Path
import stat
import signal
import re
import subprocess
import sys
import time

if __package__:
    from . import bootstrap_cli_progress as cli
    from .bootstrap_progress import Phase, Progress, Status, emit_result
    from .bootstrap_process import Cancellation, ChildRunner, InitializationCancelled, UnexpectedApproval
else:
    import bootstrap_cli_progress as cli
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
RECOVERY_NGINX_WAIT = 60
RECOVERY_NGINX_POLL = 2



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
            # Nested retry events are based on the actual existing attempt;
            # this never changes the number of probes or their return values.
            if attempt:
                with cli.step(f"Retry {name} {attempt + 1}/{READ_ONLY_ATTEMPTS}") as outcome:
                    result = command(STAGE_BY_NAME[name][0], quiet=True)
                    if not result:
                        outcome.status = Status.NOT_READY
            else:
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


def observed_check(name):
    # Same checked() implementation and retry budget as before. The status
    # represents the actual boolean result, never a parsed child message.
    with cli.step("Check " + name) as outcome:
        ready = checked(name)
        if not ready:
            outcome.status = Status.NOT_READY
        return ready


def require(name):
    if not observed_check(name):
        raise UnifiedStartupError(
            f"{name} not ready; inspect its --check-* output before proceeding"
        )
    cli.legacy(f"PASS: {name}")


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
                    cli.legacy(f"KEEP: {container_name}")
                    cli.substep("Keep " + container_name, Status.CACHED)
                    continue
                cli.substep("Start " + container_name, Status.RUNNING)
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
                cli.legacy(f"START: {container_name}")
                cli.substep("Start " + container_name, Status.DONE)
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
        if attempt and time.monotonic() >= deadline:
            break
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
            cli.notice("WAIT: trusted backend-agent health after Vault unseal (up to 60s polling)")
        time.sleep(min(RECOVERY_AGENT_POLL, remaining))
    raise UnifiedStartupError("Backend-agent health wait expired; inspect --check-backend-agent; no restart attempted")


def recovery_nginx_probe(expected_id, timeout):
    """Bound the complete read-only child probe, including dependency checks.

    Isolate the process group so timeout or Ctrl+C can stop the local probe
    and its subprocesses. Never capture or forward child output.
    """
    tls = Path(os.environ.get(
        "XDG_STATE_HOME", str(Path.home() / ".local/state")
    )) / "transcendence/dev/tls"
    argv = [sys.executable, str(ROOT / "scripts/dev/bootstrap_nginx.py"),
            "check-recovery", "--expected-id", expected_id]
    try:
        child = subprocess.Popen(
            argv, cwd=ROOT, env={**os.environ, "FT_DEV_TLS_DIR": str(tls)},
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True,
        )
    except OSError:
        raise UnifiedStartupError("Cannot execute trusted Nginx recovery probe") from None
    try:
        return child.wait(timeout=timeout)
    except (subprocess.TimeoutExpired, KeyboardInterrupt) as exc:
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        child.wait()
        if isinstance(exc, KeyboardInterrupt):
            raise
        raise UnifiedStartupError(
            "Nginx recovery readiness budget expired; inspect --check-nginx; no restart attempted"
        ) from None


def wait_recovery_nginx(targets):
    """Extra bounded checks only for a verified running gateway in recovery."""
    import bootstrap_nginx as nginx

    if len(targets) != 1:
        raise UnifiedStartupError("Unexpected Nginx recovery inventory")
    name, expected_id, _, state = targets[0]
    if (name != nginx.NAME or state != "running"
            or not isinstance(expected_id, str)
            or not re.fullmatch(r"[0-9a-f]{64}", expected_id)):
        raise UnifiedStartupError("Unexpected Nginx recovery target")

    deadline = time.monotonic() + RECOVERY_NGINX_WAIT
    routes = {code: path for path, code in nginx.RECOVERY_502.items()}
    attempts = RECOVERY_NGINX_WAIT // RECOVERY_NGINX_POLL + 1
    for attempt in range(attempts):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        with cli.step(f"Recovery Nginx HTTPS probe {attempt + 1}") as outcome:
            code = recovery_nginx_probe(expected_id, remaining)
            if time.monotonic() >= deadline:
                raise UnifiedStartupError(
                    "Nginx recovery readiness budget expired; inspect --check-nginx; no restart attempted"
                )
            if code == 0:
                return
            if code not in routes:
                raise UnifiedStartupError(
                    "Nginx recovery check failed outside retryable HTTP 502; "
                    "inspect --check-nginx; no restart attempted"
                )
            outcome.status = Status.NOT_READY
        # Only the fixed status/route contract is observable, never child text.
        cli.notice(f"WAIT: Nginx HTTPS {routes[code]} returned HTTP 502 "
                   "(bounded recovery readiness)")
        remaining = deadline - time.monotonic()
        if remaining <= 0 or attempt + 1 == attempts:
            break
        time.sleep(min(RECOVERY_NGINX_POLL, remaining))
    raise UnifiedStartupError(
        "Nginx recovery readiness budget expired; inspect --check-nginx; no restart attempted"
    )


def resume(name, *, recovery=False):
    if observed_check(name):
        cli.legacy(f"KEEP: {name} ready")
        return
    cli.substep("Inspect trusted " + name + " containers", Status.RUNNING)
    try:
        targets = guard_resume(name)
    except RunningNotReady as exc:
        cli.substep("Inspect trusted " + name + " containers", Status.DONE)
        if not recovery:
            raise
        if name == "backend-agent":
            with cli.step("Wait for trusted backend-agent health"):
                wait_recovery_backend_agent(exc.targets)
        elif name == "nginx":
            with cli.step("Wait for trusted Nginx HTTPS readiness"):
                wait_recovery_nginx(exc.targets)
        else:
            raise
        return
    except UnifiedStartupError:
        cli.substep("Inspect trusted " + name + " containers", Status.FAILED)
        raise
    except Exception:
        cli.substep("Inspect trusted " + name + " containers", Status.FAILED)
        raise UnifiedStartupError(f"Cannot inspect trusted {name} containers") from None
    cli.substep("Inspect trusted " + name + " containers", Status.DONE)
    cli.legacy(f"RESUME: {name} (existing containers only)")
    with cli.step("Resume existing " + name):
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
            cli.legacy("KEEP: trusted Vault container running")
            cli.substep("Keep trusted Vault container", Status.CACHED)
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
            cli.legacy("START: existing trusted Vault container")
            cli.substep("Start existing trusted Vault container", Status.DONE)
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
    cli.legacy("=== Trusted reboot recovery (manual Shamir unseal) ===")
    with cli.stage("Recovery host"):
        require("host")
    with cli.stage("Recovery resources"):
        require("resources")
    with cli.stage("Existing Vault", Phase.VERIFY):
        vault, seal = recovery_vault()
    with cli.stage("Manual Shamir unseal", Phase.VERIFY) as outcome:
        if seal["sealed"]:
            with cli.interactive("Manual Shamir unseal on the controlling terminal"):
                interactive_unseal(vault, seal)
        else:
            interactive_unseal(vault, seal)
            outcome.status = Status.SKIPPED
    with cli.stage("Vault readiness"):
        require("vault")
    # Reuse the guarded, non-provisioning application recovery path.
    start(recovery=True)
    cli.result("REBOOT RECOVERY READY")

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
    cli.legacy("=== Vault-backed routine startup (no provisioning or migrations) ===")
    with cli.stage("Host configuration", success=Status.CACHED):
        require("host")
    with cli.stage("Docker resources", success=Status.CACHED):
        require("resources")
    with cli.stage("Vault seal guard"):
        vault_seal_guard()
    # Report optional gateway drift without preventing core recovery after a reboot.
    with cli.stage("Optional Vault UI") as outcome:
        if not observed_check("vault-ui"):
            outcome.status = Status.NOT_READY
            cli.notice("WARN: optional Vault UI state unhealthy; run make vault-ui-status")
    # Preserve short-circuit order and the existing transitive fast path.
    with cli.stage("Application readiness") as outcome:
        ready = (
            observed_check("nginx")
            and observed_check("prometheus")
            and observed_check("grafana")
            and observed_check("grafana-https")
        )
        outcome.status = Status.CACHED if ready else Status.NOT_READY
    with cli.stage("Resume existing services", Phase.VERIFY) as outcome:
        if ready:
            outcome.status = Status.SKIPPED
            cli.legacy("KEEP: complete Vault-backed stack ready")
        else:
            # Recover only existing trusted stopped containers in dependency order.
            for name, _, _ in STAGES[2:]:
                if name == "vault-ui":
                    cli.legacy("SKIP: optional vault-ui lifecycle")
                    cli.substep("Optional Vault UI lifecycle", Status.SKIPPED)
                    continue
                if name == "vault":
                    require(name)
                elif name in RESUMABLE:
                    if recovery and name in ("backend-agent", "nginx"):
                        resume(name, recovery=True)
                    else:
                        resume(name)
                else:
                    require(name)
    cli.result("UNIFIED STARTUP READY")


def check():
    cli.legacy("=== Unified read-only readiness check ===")
    with cli.stage("Host configuration"):
        require("host")
    with cli.stage("Docker resources"):
        require("resources")
    # Optional gateway intent and the original transitive checks are unchanged.
    with cli.stage("Vault UI lifecycle"):
        require("vault-ui")
    with cli.stage("Application HTTPS"):
        require("nginx")
    with cli.stage("Prometheus readiness"):
        require("prometheus")
    with cli.stage("Grafana readiness"):
        require("grafana")
    with cli.stage("Grafana HTTPS"):
        require("grafana-https")
    cli.result("UNIFIED STACK READY (read-only)")


def health_report():
    """Read-only fast path and existing fail-closed failure localization."""
    cli.legacy("=== Vault-backed development health report (read-only) ===")
    for name, label in (("host", "Host configuration"), ("resources", "Docker resources")):
        with cli.stage(label):
            if not observed_check(name):
                cli.notice(f"FAIL: {name}; inspect {STAGE_BY_NAME[name][0]}")
                raise UnifiedStartupError(f"Health report failed at {name}")
            cli.legacy(f"PASS: {name}")

    with cli.stage("Application HTTPS"):
        if not observed_check("nginx"):
            cli.notice("WARN: end-to-end check failed; locating the first failing dependency")
            for name, _, _ in STAGES[2:]:
                if not observed_check(name):
                    cli.notice(f"FAIL: {name}; inspect {STAGE_BY_NAME[name][0]}")
                    raise UnifiedStartupError(
                        f"Health report failed at {name} (or one of its checked dependencies)"
                    )
                cli.legacy(f"PASS: {name}")
            # An independently passing second pass does not establish health.
            raise UnifiedStartupError(
                "Intermittent readiness failure: initial HTTPS check failed; "
                "all individual read-only checks later passed"
            )
        cli.legacy("PASS: end-to-end Nginx HTTPS, application and trusted dependencies")

    with cli.stage("Vault UI lifecycle"):
        if not observed_check("vault-ui"):
            cli.notice("FAIL: vault-ui; inspect --check-vault-ui-optional")
            raise UnifiedStartupError("Health report failed at vault-ui")
        cli.legacy("PASS: Vault UI expected lifecycle state and Docker isolation")

    with cli.stage("Prometheus readiness"):
        if not observed_check("prometheus"):
            cli.notice("FAIL: prometheus; inspect --check-prometheus")
            raise UnifiedStartupError("Health report failed at prometheus")
        cli.legacy("PASS: Prometheus readiness and trusted identity")

    with cli.stage("Grafana readiness"):
        if not observed_check("grafana"):
            for dependency in ("grafana-secrets", "grafana-agent"):
                if not observed_check(dependency):
                    cli.notice(f"FAIL: {dependency}; inspect {STAGE_BY_NAME[dependency][0]}")
                    raise UnifiedStartupError(f"Health report failed at {dependency}")
            cli.notice("FAIL: grafana; inspect --check-grafana")
            raise UnifiedStartupError(
                "Health report failed at grafana or its checked dependencies"
            )
        cli.legacy("PASS: Grafana authentication and provisioning")

    with cli.stage("Grafana HTTPS"):
        if not observed_check("grafana-https"):
            cli.notice("FAIL: grafana-https; inspect --check-grafana-https")
            raise UnifiedStartupError("Health report failed at grafana-https")
        cli.legacy("PASS: Grafana HTTPS and WAF protection")
    cli.result("DEVELOPMENT HEALTHY (read-only)")

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


# Fixed stage labels reflect the existing command branches, not extra probes.
START_LABELS = (
    "Host configuration", "Docker resources", "Vault seal guard",
    "Optional Vault UI", "Application readiness", "Resume existing services",
)
RECOVERY_LABELS = (
    "Recovery host", "Recovery resources", "Existing Vault",
    "Manual Shamir unseal", "Vault readiness",
) + START_LABELS
CHECK_LABELS = (
    "Host configuration", "Docker resources", "Vault UI lifecycle",
    "Application HTTPS", "Prometheus readiness", "Grafana readiness", "Grafana HTTPS",
)
HEALTH_LABELS = (
    "Host configuration", "Docker resources", "Application HTTPS",
    "Vault UI lifecycle", "Prometheus readiness", "Grafana readiness", "Grafana HTTPS",
)
ROUTINE_PRESENTATION = {
    "start": ("Starting ft_transcendence", ("Startup lock",) + START_LABELS),
    "recover": ("Recovering ft_transcendence", ("Startup lock",) + RECOVERY_LABELS),
    "check": ("Checking ft_transcendence readiness", CHECK_LABELS),
    "health-report": ("Checking ft_transcendence health", HEALTH_LABELS),
}


def routine(mode, *, shell_entry=False):
    with cli.stage("System prerequisites"):
        # The public shell entry used to run this check before Python, then
        # preflight_once ran the shell guard again. Keep both, in that order.
        if shell_entry:
            with cli.step("Check installed system prerequisites"):
                completed = subprocess.run(
                    [str(ROOT / "scripts/install-deps.sh"), "--check"], cwd=ROOT,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
                )
                if completed.returncode:
                    raise UnifiedStartupError(
                        "Dependencies not ready; inspect ./scripts/install-deps.sh --check; "
                        "use make initialize on a fresh VM"
                    )
        with cli.step("Verify trusted shell preflight"):
            preflight_once()
    if mode == "check":
        check()
    elif mode == "health-report":
        health_report()
    else:
        # Acquire the same lock, at the same point, and hold it throughout.
        with ExitStack() as stack:
            with cli.stage("Startup lock"):
                stack.enter_context(exclusive_orchestrator_lock())
            if mode == "recover":
                recover()
            else:
                start()


def main(argv=None, *, render=False):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("initialize", "start", "check", "recover", "health-report"))
    parser.add_argument("--shell-entry", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.shell_entry and args.mode == "initialize":
        parser.error("shell-entry is only for routine commands")
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
        if render:
            header, labels = ROUTINE_PRESENTATION[args.mode]
            with cli.presentation(("System prerequisites",) + labels, header,
                                  allow_not_ready=args.mode in ("start", "recover")):
                routine(args.mode, shell_entry=args.shell_entry)
        else:
            routine(args.mode, shell_entry=args.shell_entry)
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
    except KeyboardInterrupt:
        if args.mode == "initialize":
            raise
        emit_result("UNIFIED BOOTSTRAP STOPPED (interrupted; no automatic rollback)",
                    Status.CANCELLED, stream=sys.stderr)
        return 130
    except OSError:
        print("ERROR: Local bootstrap operation failed; inspect the active stage", file=sys.stderr)
        return 1
    except UnifiedStartupError as exc:
        print(f"ERROR: {exc}", file=sys.stderr, flush=True)
        if args.mode == "initialize" or render:
            emit_result("UNIFIED BOOTSTRAP STOPPED (no resources removed)", Status.FAILED, stream=sys.stderr)
        else:
            print("RESULT: UNIFIED BOOTSTRAP STOPPED (no resources removed)", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(render=True))
