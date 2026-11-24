"""Bounded-output child execution for development initialization only."""
from collections import Counter
from contextlib import contextmanager
import os
import re
import selectors
import signal
import subprocess
import time

if __package__:
    from .bootstrap_progress import Status
    from .bootstrap_images import IMAGES
else:
    from bootstrap_progress import Status
    from bootstrap_images import IMAGES


class InitializationCancelled(Exception):
    def __init__(self, signum):
        self.signum = signum
        super().__init__('Initialization interrupted; inspect existing state')


class UnexpectedApproval(Exception):
    """An approval prompt in this path is a regression, not a failed probe."""


class Cancellation:
    def __init__(self):
        self.signum = None

    @contextmanager
    def handling(self):
        def remember(signum, _frame):
            if self.signum is None:
                self.signum = signum
        previous = {sig: signal.signal(sig, remember)
                    for sig in (signal.SIGINT, signal.SIGTERM)}
        try:
            yield self
        finally:
            for sig, handler in previous.items():
                signal.signal(sig, handler)

    def check(self):
        if self.signum is not None:
            raise InitializationCancelled(self.signum)


# Only static, reviewed messages leave the child-output boundary. Arbitrary
# strings, command lines, paths and credentials never reach the terminal.
NOTICES = {
    b'SECURITY: Membership in the docker group effectively grants root privileges.':
        'SECURITY: Docker group membership grants root-equivalent privileges.',
    b'ACTION REQUIRED: Docker group membership exists but is not active in this session.':
        'ACTION REQUIRED: Sign out and back in, then rerun make initialize.',
    b'ACTION REQUIRED: Sign out and back in (or reboot the VM), then rerun:':
        'ACTION REQUIRED: Sign out and back in, then rerun make initialize.',
    b'NEVER paste or commit this file. It is NOT safe for a shared/production Vault.':
        'SECURITY: Protect the private Vault recovery file outside the VM and Git.',
    b'SECURITY: Contains root token and all three shares; protect it outside the VM.':
        'SECURITY: Protect the private Vault recovery file outside the VM and Git.',
    b'WARNING: Recovery material exists but Vault container is missing':
        'WARNING: Recovery material exists but Vault container is missing; inspect existing state.',
    b'ERROR: Unknown untrusted local CA; use explicit host preparation after review':
        'ERROR: Unknown untrusted local CA; review using --prepare-host.',
    b'ERROR: Local CA differs from its initialization record':
        'ERROR: Local CA identity differs from the recorded fingerprint.',
    b'ERROR: Incomplete local CA; refusing replacement':
        'ERROR: Incomplete local CA; refusing replacement.',
    b'ERROR: Incomplete browser TLS state; refusing replacement':
        'ERROR: Incomplete browser TLS state; refusing replacement.',
    b'ERROR: Unexpected Docker context; manual Docker access review required':
        'ERROR: Unexpected Docker context; manual Docker access review required.',
    b'ERROR: Unexpected DOCKER_HOST; manual Docker access review required':
        'ERROR: Remote or unexpected Docker endpoint; manual review required.',
    b'ERROR: Unexpected Docker socket ownership':
        'ERROR: Unexpected Docker socket ownership.',
    b'ERROR: Vault is sealed: use ./scripts/dev-start.sh for manual unseal':
        'ERROR: Vault is sealed; use make recover with existing shares.',
    b'CANCELLED: Vault recovery material persisted privately; review Vault seal state before continuing':
        'CANCELLED: Vault recovery material persisted privately; review seal state.',
}


# Exact source messages only; no prefixes containing arbitrary child values.
EVENTS = {
    "PASS: All base packages installed": ("Verify base packages", Status.DONE),
    "PASS: Docker Engine, Compose and Buildx available": ("Verify Docker tooling", Status.DONE),
    "PASS: Docker daemon accessible without sudo": ("Verify local Docker access", Status.DONE),
    "OK: Docker daemon accessible without sudo": ("Verify local Docker access", Status.DONE),
    "RESULT: PREREQUISITES READY (read-only)": ("Verify prerequisites", Status.DONE),
    "RESULT: DEPENDENCIES READY": ("Prepare system dependencies", Status.DONE),
    "INFO: Installing official Docker Engine / Compose / Buildx": ("Install Docker Engine, Compose and Buildx", Status.RUNNING),
    "INFO: Starting Docker service": ("Start Docker daemon", Status.RUNNING),
    "RESULT: REQUIRED DOCKER IMAGES READY": ("Verify required images", Status.DONE),
    "KEEP: All required Docker images already available.": ("Reuse existing Docker images", Status.CACHED),
    "PASS: Local transcendence.test domain": ("Verify development hostname", Status.DONE),
    "PASS: Browser-facing Nginx TLS certificate": ("Verify browser TLS certificate", Status.DONE),
    "PASS: Local CA trust": ("Verify local CA trust", Status.DONE),
    "PASS: Dedicated Nginx TLS group and ACL": ("Verify Nginx TLS permissions", Status.DONE),
    "PASS: Existing Vault TLS chain, hostname and expiry": ("Verify Vault TLS", Status.DONE),
    "RESULT: HOST PREPARATION READY": ("Verify host preparation", Status.DONE),
    "PASS: Compose resource inventory and external network contract": ("Verify Compose resource inventory", Status.DONE),
    "RESULT: DOCKER RESOURCES READY (read-only)": ("Verify Docker resources", Status.DONE),
    "RESULT: DOCKER RESOURCES READY": ("Verify Docker resources", Status.DONE),
    "STEP: Reserve private Vault recovery file": ("Reserve private Vault recovery file", Status.RUNNING),
    "PASS: Private Vault recovery file reserved": ("Reserve private Vault recovery file", Status.DONE),
    "STEP: Initialize Vault and persist recovery material": ("Initialize Vault and persist recovery material", Status.RUNNING),
    "PASS: Vault recovery material persisted": ("Initialize Vault and persist recovery material", Status.DONE),
    "PASS: Vault initialized (Shamir 2-of-3)": ("Verify Vault Shamir initialization", Status.DONE),
    "STEP: Initial Vault unseal": ("Initial Vault unseal", Status.RUNNING),
    "PASS: Vault initialized, unsealed and using Raft": ("Initial Vault unseal", Status.DONE),
    "KEEP: Vault is already initialized; never reinitializing": ("Reuse existing Vault", Status.CACHED),
    "RESULT: VAULT READY (read-only)": ("Verify Vault identity and readiness", Status.DONE),
    "PASS: Vault configuration check completed (KV v2, ACLs, four AppRoles)": ("Verify Vault KV, ACLs and AppRoles", Status.DONE),
    "PASS: Vault configuration apply completed (KV v2, ACLs, four AppRoles)": ("Prepare Vault KV, ACLs and AppRoles", Status.DONE),
    "PRESERVED: Vault backend database credential": ("Preserve backend database credential", Status.CACHED),
    "PRESERVED: Vault migrator database credential": ("Preserve migrator database credential", Status.CACHED),
    "CREATED: Vault backend database credential": ("Create backend database credential", Status.DONE),
    "CREATED: Vault migrator database credential": ("Create migrator database credential", Status.DONE),
    "PASS: Vault database credentials check verified": ("Verify Vault database credentials", Status.DONE),
    "PASS: Vault database credentials apply verified": ("Prepare Vault database credentials", Status.DONE),
    "PASS: Fresh PostgreSQL cluster initialized without persistent bootstrap password": ("Initialize PostgreSQL cluster", Status.DONE),
    "KEEP: Existing PostgreSQL 18 cluster; no reinitialization": ("Reuse PostgreSQL cluster", Status.CACHED),
    "PASS: PostgreSQL 18, database identity, SCRAM host auth and passwordless-disabled admin": ("Verify PostgreSQL identity and SCRAM", Status.DONE),
    "PASS: PostgreSQL SCRAM roles and schema privileges": ("Verify PostgreSQL role permissions", Status.DONE),
    "PASS: PostgreSQL SCRAM roles and schema privileges (metadata only)": ("Verify PostgreSQL role permissions", Status.DONE),
    "PASS: Existing Vault-to-PostgreSQL SCRAM provisioning workflow": ("Prepare PostgreSQL SCRAM roles", Status.DONE),
    "RUN: Existing Prisma migration and grants workflow": ("Run Prisma migration and grants workflow", Status.RUNNING),
    "PASS: Existing Prisma migration and grants workflow": ("Run Prisma migration and grants workflow", Status.DONE),
    "KEEP: Prisma migrations and PostgreSQL grants": ("Reuse existing Prisma migrations", Status.CACHED),
    "RESULT: PRISMA MIGRATIONS READY (read-only)": ("Verify Prisma migration history and grants", Status.DONE),
    "PASS: Backend UID/GID can read Vault-rendered DSN (content not displayed)": ("Verify backend credential access", Status.DONE),
    "PASS: Backend Vault Agent healthy": ("Verify backend Vault Agent", Status.DONE),
    "PASS: NestJS /api/v1/health confirms PostgreSQL and migrations": ("Verify NestJS health", Status.DONE),
    "PASS: Vault-backed NestJS backend healthy": ("Verify NestJS health", Status.DONE),
    "PASS: Collector health, snapshot and Docker Proxy GET access": ("Verify collector and Docker proxy", Status.DONE),
    "PASS: Graph Engine HTTP health": ("Verify graph engine", Status.DONE),
    "PASS: Backend can reach Collector and Graph Engine": ("Verify backend service connectivity", Status.DONE),
    "PASS: React/Vite HTML, entry module and backend API proxy": ("Verify React frontend", Status.DONE),
    "PASS: Nginx TLS verified, Vite HTML/module and NestJS API via HTTPS": ("Verify Nginx HTTPS application routing", Status.DONE),
    "PASS: Loopback HTTP 8080 redirects to HTTPS 8443": ("Verify HTTP to HTTPS redirect", Status.DONE),
    "RESULT: PROMETHEUS READY (read-only)": ("Verify Prometheus metrics readiness", Status.DONE),
    "RETRY: Prometheus HTTP readiness": ("Retry Prometheus metrics readiness", Status.RUNNING),
    "PASS: Prometheus HTTP readiness retry": ("Retry Prometheus metrics readiness", Status.DONE),
    "MISSING: Initial Grafana Vault credentials": ("Initial Grafana credentials not yet created", Status.MISSING),
    "MISSING: Required Docker images": ("Verify required images", Status.MISSING),
    "NOT READY: Docker daemon inaccessible (service or user permissions)": ("Verify local Docker access", Status.NOT_READY),
    "RESULT: PREREQUISITES NOT READY (read-only)": ("Verify prerequisites", Status.NOT_READY),
    "CREATED: Grafana Vault credentials": ("Create Grafana credentials", Status.DONE),
    "PRESERVED: Grafana Vault credentials": ("Preserve Grafana credentials", Status.CACHED),
    "PASS: Grafana credential storage permissions": ("Verify Grafana credential storage", Status.DONE),
    "PASS: Grafana UID/GID can read rendered secrets": ("Verify Grafana rendered credential access", Status.DONE),
    "PASS: Actual Grafana Vault secret delivery": ("Verify Grafana Vault secret delivery", Status.DONE),
    "STEP: Verify Grafana container identity": ("Verify Grafana container identity", Status.RUNNING),
    "PASS: Grafana container identity and privileges": ("Verify Grafana container identity", Status.DONE),
    "STEP: Verify Grafana prerequisites": ("Verify Grafana prerequisites", Status.RUNNING),
    "PASS: Grafana prerequisites verified": ("Verify Grafana prerequisites", Status.DONE),
    "STEP: Verify Grafana HTTP health": ("Verify Grafana HTTP health", Status.RUNNING),
    "PASS: Grafana HTTP health": ("Verify Grafana HTTP health", Status.DONE),
    "STEP: Verify Grafana authentication and provisioning": ("Verify Grafana authentication and provisioning", Status.RUNNING),
    "PASS: Grafana authentication and provisioning": ("Verify Grafana authentication and provisioning", Status.DONE),
    "PASS: Grafana anonymous API access rejected": ("Verify Grafana anonymous access rejection", Status.DONE),
    "PASS: Persistent Grafana PromQL CRS policy.": ("Verify Grafana PromQL CRS policy", Status.DONE),
    "PASS: Grafana HTTPS gateway identity and policy.": ("Verify Grafana HTTPS gateway identity", Status.DONE),
    "PASS: Grafana HTTPS login and canonical redirect.": ("Verify Grafana HTTPS login and redirect", Status.DONE),
    "PASS: Grafana HTTPS health and database.": ("Verify Grafana HTTPS health and database", Status.DONE),
    "PASS: Grafana HTTPS anonymous access rejected.": ("Verify Grafana HTTPS anonymous access rejection", Status.DONE),
    "PASS: Grafana HTTPS WAF blocking.": ("Verify Grafana HTTPS WAF blocking", Status.DONE),
    "RETRY: Grafana HTTPS upstream readiness": ("Retry Grafana HTTPS upstream readiness", Status.RUNNING),
    "PASS: Grafana HTTPS readiness retry": ("Retry Grafana HTTPS upstream readiness", Status.DONE),
}
EVENTS.update({
    "STEP: Install missing Ubuntu packages": ("Install missing Ubuntu packages", Status.RUNNING),
    "PASS: Missing Ubuntu packages installed": ("Install missing Ubuntu packages", Status.DONE),
    "PASS: Official Docker packages installed": ("Install Docker Engine, Compose and Buildx", Status.DONE),
    "PASS: Docker daemon started": ("Start Docker daemon", Status.DONE),
})
EVENTS = {line.encode("ascii"): event for line, event in EVENTS.items()}
for package in ("git", "make", "python3", "openssl", "curl", "ca-certificates",
                "util-linux", "acl", "mkcert", "libnss3-tools", "Docker CLI",
                "Docker Compose plugin", "Docker Buildx plugin"):
    EVENTS[("OK: " + package).encode("ascii")] = ("Check " + package, Status.DONE)
    EVENTS[("MISSING: " + package).encode("ascii")] = ("Check " + package, Status.MISSING)
# Reviewed literal inventory. Never reflect arbitrary Docker resource names.
for kind, names in (
    ("network", ("transcendence-vault-dev_vault_private", "transcendence-app-dev_default",
                 "transcendence-app-dev_collector_control")),
    ("volume", ("transcendence-vault-dev_vault_data", "transcendence-vault-dev_backend_agent_auth",
                "transcendence-vault-dev_backend_rendered", "transcendence-app-dev_postgres_data",
                "transcendence-app-dev_prometheus_data", "transcendence-app-dev_grafana_data",
                "transcendence-app-dev_grafana_vault_auth", "transcendence-app-dev_grafana_vault_rendered",
                "transcendence-app-dev_migrator_vault_auth", "transcendence-app-dev_migrator_vault_rendered",
                "transcendence-app-dev_postgres_provisioner_vault_auth",
                "transcendence-app-dev_postgres_provisioner_rendered")),
):
    for name in names:
        EVENTS[(f"CREATED: {kind} {name}").encode("ascii")] = (f"Create {kind} {name}", Status.DONE)
for name in ("transcendence-vault-dev-vault-1", *(
    "transcendence-app-dev-" + service + "-1"
    for service in ("postgres", "vault-backend-agent", "backend", "docker-proxy",
                    "collector", "graph-engine", "frontend", "nginx", "prometheus",
                    "vault-grafana-agent", "grafana")
)):
    for prefix, label, status in (("CREATED", "Create", Status.DONE),
                                  ("KEEP", "Reuse", Status.CACHED),
                                  ("START", "Start", Status.DONE)):
        EVENTS[(f"{prefix}: {name}").encode("ascii")] = (f"{label} {name}", status)
for image in IMAGES:
    EVENTS[("MISSING: " + image).encode("ascii")] = ("Pull " + image, Status.MISSING)
    EVENTS[("PULL: " + image).encode("ascii")] = ("Pull " + image, Status.RUNNING)
    EVENTS[("PASS: " + image).encode("ascii")] = ("Pull " + image, Status.DONE)

SAFE_ERRORS = {
    "ERROR: Host preparation incomplete",
    "ERROR: Docker resources incomplete",
    "ERROR: Grafana Vault Agent incomplete",
    "ERROR: Prometheus incomplete",
    "ERROR: Grafana credential check failed; refusing provisioning",
    "ERROR: Grafana Vault Agent is not ready",
    "ERROR: Required Docker resources missing",
    "ERROR: Required Docker resources missing; use explicit resource preparation",
    "ERROR: Trusted support services are not ready",
    "ERROR: Prometheus HTTP/metrics probe not ready or unavailable",
    "ERROR: Prometheus HTTP/metrics readiness failed after bounded checks",
    "ERROR: Prometheus container missing",
    "ERROR: Prometheus is not running",
    "ERROR: Unexpected Prometheus identity",
    "ERROR: Unexpected Prometheus privileges or network",
    "ERROR: Unexpected Prometheus mounts",
    "ERROR: Unexpected Prometheus credentials",
    "ERROR: Grafana container missing",
    "ERROR: Unexpected Grafana identity",
    "ERROR: Unexpected Grafana runtime privileges",
    "ERROR: Unexpected Grafana mounts",
    "ERROR: Unexpected Grafana environment override",
    "ERROR: Unexpected application credentials",
    "ERROR: Grafana database or version not ready",
    "ERROR: Grafana authentication not verified",
    "ERROR: Prometheus datasource mismatch",
    "ERROR: Grafana readiness timeout",
    "ERROR: Grafana stopped unexpectedly",
    "ERROR: Grafana is not running",
    "ERROR: Grafana Vault Agent not healthy",
    "ERROR: Grafana AppRole credentials missing",
    "ERROR: Grafana Vault secrets are not ready",
    "ERROR: Unexpected Grafana Agent identity",
    "ERROR: Unexpected Grafana Agent runtime privileges",
    "ERROR: Required Grafana secrets are missing",
    "ERROR: Trusted Vault configuration not ready",
    "ERROR: Invalid Grafana helper result",
    "ERROR: Grafana secret helper unavailable or timed out",
    "ERROR: Grafana secrets check failed; raw output withheld",
    "ERROR: Grafana secrets apply failed; raw output withheld",
    "ERROR: Nginx gateway missing.",
    "ERROR: Nginx gateway is not running.",
    "ERROR: Nginx WAF blocking mode not enabled.",
    "ERROR: Nginx runtime configuration differs from template.",
    "ERROR: Required Grafana logging protection missing.",
    "ERROR: Trusted PromQL CRS source unavailable.",
    "ERROR: Trusted PromQL CRS source is empty.",
    "ERROR: Runtime PromQL CRS rules differ from trusted source.",
    "ERROR: Grafana HTTPS upstream temporarily unavailable.",
    "ERROR: Grafana canonical redirect mismatch.",
    "ERROR: Grafana login HTML unavailable or incomplete.",
    "ERROR: Grafana HTTPS health unavailable.",
    "ERROR: Invalid Grafana HTTPS health response.",
    "ERROR: Grafana database or version verification failed.",
    "ERROR: Grafana anonymous API access was not rejected.",
    "ERROR: Grafana inbound WAF blocking not verified.",
    "ERROR: Invalid Grafana HTTPS response.",
    "ERROR: Invalid Grafana HTTPS status.",
    "ERROR: Vault is not initialized",
    "ERROR: Unexpected Vault container identity/Compose provenance",
    "ERROR: Recovery file has unsafe ownership/permissions",
    "ERROR: Protected Vault recovery file is missing",
    "ERROR: Prisma migration history drift; manual review required",
    "ERROR: Another provisioning workflow is running",
    "ERROR: Unexpected Docker context; manual Docker access review required",
    "ERROR: Unexpected DOCKER_HOST; manual Docker access review required",
    "ERROR: Docker group is active but access is denied; review Docker socket permissions manually.",
    "ERROR: Nginx command unavailable or timed out: curl",
    "ERROR: Nginx command unavailable or timed out: docker",
}
for message in SAFE_ERRORS:
    NOTICES.setdefault(message.encode("ascii"), message)
for command in ("curl", "docker"):
    for code in (1, 2, 6, 7, 22, 28, 35, 56, 60, 125, 126, 127):
        message = f"ERROR: Nginx command failed: {command} (exit {code}; output withheld)"
        NOTICES[message.encode("ascii")] = message
# These exact shell notices accompany the dedicated installer exit code 2.
SESSION_REFRESH = {
    b"ACTION REQUIRED: Docker group membership exists but is not active in this session.",
    b"ACTION REQUIRED: Sign out and back in (or reboot the VM), then rerun:",
}


class Diagnostics:
    def __init__(self):
        self.counts = Counter()
        self.notices = []
        self.buffers = {0: b'', 1: b''}
        self.truncated = {0: False, 1: False}
        self.prompt_seen = False
        self.session_refresh = False
        self.events = []
        self.missing_seen = False

    def line(self, stream, value):
        if not value:
            return
        self.counts['stdout' if stream == 0 else 'stderr'] += 1
        if re.search(rb'\[[yYnN]/[yYnN]\]', value):
            self.prompt_seen = True
        if value in SESSION_REFRESH:
            self.session_refresh = True
        if value in EVENTS:
            event = EVENTS[value]
            if event[1] is Status.MISSING:
                self.missing_seen = True
            if len(self.events) < 128:
                self.events.append(event)
        if value in NOTICES:
            notice = NOTICES[value]
            if notice.startswith("ERROR:"):
                self.counts["error"] += 1
                self.counts["reviewed_error"] += 1
            elif notice.startswith(("SECURITY:", "WARNING:", "ACTION REQUIRED:")):
                self.counts["warning"] += 1
                self.counts["reviewed_warning"] += 1
            if notice not in self.notices:
                self.notices.append(notice)
        elif value == b"IMPORTANT: Recovery material stored privately at:" or value.startswith(b"IMPORTANT: Recovery material stored privately at: "):
            # The path is sensitive and deliberately not reflected; the fixed
            # security notice already covers the meaning of this message.
            self.counts["warning"] += 1
            self.counts["reviewed_warning"] += 1
        elif re.match(rb'^(WARN(?:ING)?[: ]|\[WARN(?:ING)?\]|SECURITY:|IMPORTANT:|ACTION REQUIRED:)', value):
            self.counts['warning'] += 1
        elif re.match(rb'^(ERROR:|\[ERROR\]|FAIL:|RESULT: .*STOPPED)', value):
            self.counts['error'] += 1

    def feed(self, stream, data):
        for i, fragment in enumerate(data.split(b'\n')):
            if i:
                if not self.truncated[stream]:
                    self.line(stream, self.buffers[stream].rstrip(b'\r'))
                else:
                    self.counts['oversized'] += 1
                self.buffers[stream] = b''
                self.truncated[stream] = False
            if not self.truncated[stream]:
                combined = self.buffers[stream] + fragment
                if len(combined) > 4096:
                    self.truncated[stream] = True
                    self.buffers[stream] = b''
                else:
                    self.buffers[stream] = combined
                    if re.search(rb'\[[yYnN]/[yYnN]\]', combined):
                        self.prompt_seen = True

    def finish(self, stream):
        if self.truncated[stream]:
            self.counts['oversized'] += 1
        else:
            self.line(stream, self.buffers[stream].rstrip(b'\r'))
        self.buffers[stream] = b''


class ChildRunner:
    def __init__(self, progress, cancellation):
        self.progress = progress
        self.cancellation = cancellation
        self.verbose = os.environ.get('FT_DEV_BOOTSTRAP_VERBOSE') == '1'
        self.last_returncode = None
        self.last_diagnostics = None
        self.last_operation = None

    def report_failure(self):
        """Expose reviewed reasons from the final failed probe only."""
        if self.last_diagnostics is None:
            return
        for notice in self.last_diagnostics.notices:
            if notice.startswith("ERROR:") and not self.verbose:
                self.progress.notice(notice)
        if self.last_operation is not None:
            self.progress.substep(self.last_operation, Status.FAILED)
        if not self.verbose:
            counts = self.last_diagnostics.counts
            if counts["error"] > counts["reviewed_error"]:
                self.progress.notice("ERROR: Additional verification diagnostics withheld; use the indicated read-only check.")
            self.progress.notice("Verification failed; use the indicated read-only check for details.")

    def run(self, argv, *, cwd, env=None, pass_fds=(), foreground=False,
            critical=False, probe=False, operation="Execute reviewed operation",
            pause_for_auth=True, probe_guard_codes=()):
        self.cancellation.check()
        diagnostics = Diagnostics()
        self.last_diagnostics = diagnostics
        self.last_operation = operation
        self.last_returncode = None
        started = time.monotonic()
        reported_steps = {}
        self.progress.substep(operation, Status.RUNNING)
        # Stream Python's reviewed messages promptly instead of at process exit.
        env = {**(os.environ if env is None else env), "PYTHONUNBUFFERED": "1"}
        paused = foreground and pause_for_auth
        if paused:
            self.progress.pause()
        try:
            with subprocess.Popen(argv, cwd=cwd, env=env, pass_fds=pass_fds,
                                  stdin=subprocess.DEVNULL,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  start_new_session=not foreground) as child:
                interrupted = None
                escalated = False
                with selectors.DefaultSelector() as sel:
                    for index, pipe in enumerate((child.stdout, child.stderr)):
                        sel.register(pipe, selectors.EVENT_READ, index)
                    while sel.get_map() or child.poll() is None:
                        if self.cancellation.signum is not None or diagnostics.prompt_seen:
                            now = time.monotonic()
                            if interrupted is None:
                                interrupted = now
                                signum = self.cancellation.signum or signal.SIGINT
                                if self.cancellation.signum is not None and not paused:
                                    self.progress.notice('Cancellation requested; waiting for active operation.')
                                try:
                                    if foreground:
                                        child.send_signal(signum)
                                    else:
                                        os.killpg(child.pid, signum)
                                except ProcessLookupError:
                                    pass
                            elif not critical and not foreground and now - interrupted > 10 and not escalated:
                                escalated = True
                                try:
                                    os.killpg(child.pid, signal.SIGKILL)
                                except ProcessLookupError:
                                    pass
                        for key, _ in sel.select(0.15):
                            data = os.read(key.fd, 8192)
                            if data:
                                diagnostics.feed(key.data, data)
                            else:
                                diagnostics.finish(key.data)
                                sel.unregister(key.fileobj)
                        for label, status in diagnostics.events:
                            reported_steps[label] = status
                            self.progress.substep(label, status)
                        diagnostics.events.clear()
                        self.progress.refresh()
                    self.last_returncode = child.wait()
        finally:
            if paused:
                self.progress.resume()
        for notice in diagnostics.notices:
            if self.verbose or not probe or not notice.startswith('ERROR:'):
                self.progress.notice(notice)
        counts = diagnostics.counts
        cancelled = self.cancellation.signum is not None or self.last_returncode in (-2, -15, 130, 143)
        result = (Status.CANCELLED if cancelled else
                  Status.ACTION_REQUIRED if self.last_returncode == 2 and diagnostics.session_refresh else
                  Status.DONE if self.last_returncode == 0 else
                  Status.FAILED if diagnostics.prompt_seen or self.last_returncode in probe_guard_codes else
                  Status.MISSING if probe and diagnostics.missing_seen else
                  Status.NOT_READY if probe else Status.FAILED)
        if diagnostics.prompt_seen:
            result = Status.FAILED
        # A child exit alone does not prove every named operation completed.
        # Close outstanding events without inventing success acknowledgements.
        for label, status in reported_steps.items():
            if status is Status.RUNNING and label != operation:
                self.progress.substep(label, Status.CANCELLED if cancelled else Status.UNCONFIRMED)
        self.progress.substep(operation, result)
        elapsed = time.monotonic() - started
        if diagnostics.prompt_seen:
            self.progress.notice('ERROR: Unexpected initialization approval prompt; stopped safely.')
            raise UnexpectedApproval()
        if self.verbose:
            self.progress.notice(f"{operation}: exit {self.last_returncode} in {elapsed:.1f}s; stdout {counts['stdout']} lines, "
                                 f"stderr {counts['stderr']} lines, warnings {counts['warning']}, "
                                 f"errors {counts['error']}, oversized {counts['oversized']} (raw output withheld)")
        else:
            # Do not treat stderr itself as a failure: successful tools such
            # as mkcert and Docker write normal progress there. Preserve all
            # reviewed notices and indicate unclassified warning/error lines.
            warnings = counts["warning"] - counts["reviewed_warning"]
            errors = counts["error"] - counts["reviewed_error"]
            if warnings:
                self.progress.notice(f"WARNING: {operation}: {warnings} additional warning lines withheld; use verbose mode.")
            if not probe and errors and not cancelled:
                self.progress.notice(f"ERROR: {operation}: {errors} additional error lines withheld; use verbose mode.")
            if not probe and result is Status.FAILED:
                self.progress.notice(f"ERROR: {operation} failed (exit {self.last_returncode}); use verbose mode and the indicated check.")
            if counts["oversized"]:
                self.progress.notice(f"WARNING: {operation}: {counts['oversized']} oversized output lines withheld.")
        self.cancellation.check()
        if self.last_returncode in (-signal.SIGINT, -signal.SIGTERM, 130, 143):
            raise InitializationCancelled(signal.SIGINT if self.last_returncode in (-2, 130) else signal.SIGTERM)
        return self.last_returncode
