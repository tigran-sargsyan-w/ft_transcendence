"""Read-only plan orchestration for the unified bootstrap CLI.

The public shell entry has already validated the platform and TLS paths.
Keep the original one prerequisite check, shell inventory order and each
helper's plan operation. No additional readiness probes or lifecycle actions.
"""
from contextlib import nullcontext
import importlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

if __package__:
    from . import bootstrap_cli_progress as cli, bootstrap_plan_events as events
    from .bootstrap_progress import Status
else:
    import bootstrap_cli_progress as cli
    import bootstrap_plan_events as events
    from bootstrap_progress import Status

ROOT = Path(__file__).resolve().parents[2]
VOLUMES = (
    "transcendence-vault-dev_vault_data",
    "transcendence-vault-dev_backend_agent_auth",
    "transcendence-vault-dev_backend_rendered",
    "transcendence-app-dev_postgres_data",
    "transcendence-app-dev_prometheus_data",
    "transcendence-app-dev_grafana_data",
    "transcendence-app-dev_grafana_vault_auth",
    "transcendence-app-dev_grafana_vault_rendered",
    "transcendence-app-dev_migrator_vault_auth",
    "transcendence-app-dev_migrator_vault_rendered",
    "transcendence-app-dev_postgres_provisioner_vault_auth",
    "transcendence-app-dev_postgres_provisioner_rendered",
)
NETWORKS = (
    "transcendence-vault-dev_vault_private",
    "transcendence-app-dev_default",
    "transcendence-app-dev_collector_control",
)
PROJECTS = ("transcendence-app-dev", "transcendence-vault-dev")
HOST_CHECKS = (
    ("Development domain", "scripts.dev.domains"),
    ("Browser TLS certificate", "scripts.dev.certificates"),
    ("Local CA trust", "scripts.dev.ca_trust"),
    ("Nginx TLS group and ACL", "scripts.dev.nginx_tls_access"),
)
OPERATIONS = (
    ("Docker resource contract", "resources"),
    ("Vault", "vault"),
    ("Optional Vault UI", "vault_ui"),
    ("Vault configuration", None),
    ("Vault database secrets", "vault_secrets"),
    ("PostgreSQL", "postgres"),
    ("PostgreSQL roles", "postgres_roles"),
    ("Prisma migrations", "migrations"),
    ("Backend Vault Agent", "backend_agent"),
    ("NestJS backend", "backend"),
    ("Support services", "support_services"),
    ("React frontend", "frontend"),
    ("Nginx HTTPS", "nginx"),
    ("Prometheus", "prometheus"),
    ("Grafana secrets", "grafana_secrets"),
    ("Grafana Vault Agent", "grafana_agent"),
    ("Grafana", "grafana"),
    ("Grafana HTTPS", "grafana_https"),
)
LABELS = ("System prerequisites", "Local host configuration",
          "Existing Docker inventory") + tuple(label for label, _ in OPERATIONS)
HELPERS = frozenset(name for _, name in OPERATIONS if name is not None)
PRECEDENCE = (Status.MISSING, Status.NOT_READY, Status.UNVERIFIED,
              Status.OPTIONAL, Status.EXISTS, Status.READY)


class PlanError(Exception):
    def __init__(self, message, code=1):
        super().__init__(message)
        self.code = code if code > 0 else 128 - code


def command(args, *, output=False):
    # A failed individual host/ordinary inventory check remains a planning
    # outcome, just as in the original shell conditional checks.
    try:
        return subprocess.run(args, cwd=ROOT, check=False,
                              stdout=subprocess.PIPE if output else subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL)
    except OSError:
        return subprocess.CompletedProcess(args, 127, stdout=b"" if output else None)


def show(session, label, status):
    cli.substep(label, status)
    if status is Status.ACTION_REQUIRED:
        cli.notice("WARNING: " + label)
    elif session is None or (not session.progress.live and not session.progress.verbose):
        # Preserve useful inventory in redirected/CI/plain logs, without raw
        # helper output. Verbose mode already prints structured substeps.
        cli.notice(f"    {status.value}: {label}")


def summarize(statuses):
    for status in PRECEDENCE:
        if status in statuses:
            return status
    raise PlanError("Plan operation returned no reviewed observations")


def host_plan(session):
    results = []
    for label, module in HOST_CHECKS:
        cli.substep(label, Status.RUNNING)
        result = command([sys.executable, "-m", module, "check"])
        status = Status.READY if result.returncode == 0 else Status.NOT_READY
        show(session, label, status)
        results.append(status)

    # Equivalent to bootstrap.sh's vault_tls_status: absent is normal,
    # partial/invalid material is fatal, and chain/hostname/expiry are checked.
    tls_value = os.environ.get("FT_DEV_TLS_DIR")
    if not tls_value:
        raise PlanError("Trusted TLS path unavailable; use make plan")
    tls = Path(tls_value)
    ca = Path(os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local/share")) / "ft-transcendence/vault-ca"
    paths = (tls / "ca.crt", tls / "vault.crt", tls / "vault.key", ca / "ca.key")
    label = "Vault TLS chain hostname and expiry"
    cli.substep(label, Status.RUNNING)
    present = 0
    for path in paths:
        if os.path.exists(path):
            if not path.is_file() or path.stat().st_size == 0:
                raise PlanError("Invalid Vault TLS file; inspect trusted TLS state")
            present += 1
    if present == 0:
        status = Status.MISSING
    elif present != len(paths):
        raise PlanError("Incomplete Vault TLS state: refuse to overwrite or regenerate keys")
    else:
        if command(["openssl", "verify", "-purpose", "sslserver", "-verify_hostname", "vault",
                    "-CAfile", str(paths[0]), str(paths[1])]).returncode:
            raise PlanError("Vault TLS certificate validation failed")
        if command(["openssl", "x509", "-in", str(paths[1]), "-noout", "-checkend", "0"]).returncode:
            raise PlanError("Vault TLS certificate has expired")
        status = Status.READY
    show(session, label, status)
    results.append(status)
    ready = all(s is Status.READY for s in results)
    cli.notice("RESULT: HOST PREPARATION ALREADY READY" if ready else
               "RESULT: HOST PREPARATION NEEDED")
    return Status.READY if ready else Status.NOT_READY


def inventory(session):
    statuses = []
    for kind, names in (("volume", VOLUMES), ("network", NETWORKS)):
        for name in names:
            label = f"{kind.capitalize()} {name}"
            cli.substep(label, Status.RUNNING)
            result = command(["docker", kind, "inspect", name])
            # Preliminary inventory does not distinguish absence from failure.
            # The subsequent resource contract verifies exact missing state.
            status = Status.EXISTS if result.returncode == 0 else Status.UNVERIFIED
            show(session, label, status)
            statuses.append(status)
    for project in PROJECTS:
        label = "Containers owned by " + project
        cli.substep(label, Status.RUNNING)
        result = command(["docker", "ps", "-aq", "--filter",
                          "label=com.docker.compose.project=" + project], output=True)
        status = (Status.UNVERIFIED if result.returncode else
                  Status.EXISTS if result.stdout.strip() else Status.MISSING)
        show(session, label, status)
        statuses.append(status)
    if Status.EXISTS in statuses:
        cli.notice("INFO: Existing runtime resources detected; later provisioning must inspect them before changes")
    return summarize(statuses)


def worker(name):
    """One isolated child, with an explicit structured event stream only."""
    if name not in HELPERS:
        return 2
    # Duplicate the pipe, then silence ordinary output at FD level (including
    # subprocesses). Python's duplicated FD is non-inheritable by default.
    with os.fdopen(os.dup(1), "w", encoding="utf-8") as sink, open(os.devnull, "w") as quiet:
        os.dup2(quiet.fileno(), 1)
        os.dup2(quiet.fileno(), 2)
        events._sink = sink
        try:
            module = importlib.import_module("bootstrap_" + name)
            sys.argv = [module.__file__, "plan"]
            return module.main()
        finally:
            events._sink = None


def decode_observation(line):
    try:
        label, status, primary = json.loads(line)
        if (not isinstance(label, str) or
                not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 .:/(),_+-]{0,119}", label) or
                status not in events.STATUSES or type(primary) is not bool or
                (status == "ACTION_REQUIRED" and primary)):
            raise ValueError
        return label, Status[status], primary
    except (ValueError, TypeError, KeyError):
        raise PlanError("Invalid structured plan observation") from None


def helper_plan(name, session):
    if name not in HELPERS:
        raise PlanError("Unexpected plan helper")
    statuses = []
    with subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--worker", name],
                          cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                          text=True, encoding="utf-8") as child:
        try:
            for line in child.stdout:
                label, status, primary = decode_observation(line)
                show(session, label, status)
                if primary:
                    statuses.append(status)
            code = child.wait()
        except BaseException as exc:
            if child.poll() is None:
                child.kill()
            child.wait()
            if isinstance(exc, UnicodeError):
                raise PlanError("Invalid structured plan observation") from None
            raise
    if code:
        raise PlanError(f"Plan helper failed: {name}; inspect its standalone plan with the trusted TLS path", code)
    return summarize(statuses)


def run(session):
    with cli.stage("System prerequisites", success=Status.READY):
        with cli.step("Check installed system prerequisites"):
            if command([str(ROOT / "scripts/install-deps.sh"), "--check"]).returncode:
                raise PlanError("Dependencies not ready; inspect ./scripts/install-deps.sh --check; use make initialize on a fresh VM")
    with cli.stage("Local host configuration") as outcome:
        outcome.status = host_plan(session)
    with cli.stage("Existing Docker inventory") as outcome:
        outcome.status = inventory(session)
    for label, name in OPERATIONS:
        with cli.stage(label) as outcome:
            if name is None:
                show(session, "Vault config check available via --check-vault-config", Status.UNVERIFIED)
                outcome.status = Status.UNVERIFIED
            else:
                with cli.step("Inspect existing plan operation"):
                    outcome.status = helper_plan(name, session)
    cli.result("BOOTSTRAP PLAN COMPLETE (read-only)")


def main(*, render=True):
    try:
        context = (cli.presentation(LABELS, "Planning ft_transcendence", planning=True)
                   if render else nullcontext())
        with context as session:
            run(session)
        return 0
    except PlanError as exc:
        print(f"ERROR: {exc}", file=sys.stderr, flush=True)
        return exc.code
    except OSError:
        print("ERROR: Local plan operation failed; inspect the active stage", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    # No public mutating or arbitrary module dispatch.
    if len(sys.argv) != 3 or sys.argv[1] != "--worker":
        raise SystemExit(2)
    raise SystemExit(worker(sys.argv[2]))
