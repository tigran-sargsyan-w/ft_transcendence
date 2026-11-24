#!/usr/bin/env python3
"""Trusted Prometheus startup: no changes to existing application services."""

import argparse
import fcntl
import sys
import time

import bootstrap_backend_agent as agent
import bootstrap_postgres as pg
import bootstrap_resources as resources
import bootstrap_support_services as support

SERVICE = "prometheus"
NAME = f"{pg.PROJECT}-{SERVICE}-1"
IMAGE = "prom/prometheus:v3.15.0"
NETWORK = f"{pg.PROJECT}_default"
VOLUME = f"{pg.PROJECT}_prometheus_data"
CONFIG_DIR = pg.ROOT / "infra/monitoring/prometheus"

COMMAND = [
    "--config.file=/etc/prometheus/prometheus.yml",
    "--storage.tsdb.path=/prometheus",
    "--storage.tsdb.retention.time=7d",
    "--web.listen-address=0.0.0.0:9090",
]

HTTP_PROBE = r"""
(async () => {
  const base = 'http://prometheus:9090';
  const options = { signal: AbortSignal.timeout(5000) };

  const ready = await fetch(base + '/-/ready', options);
  if (!ready.ok) throw new Error('Prometheus is not ready');

  const response = await fetch(base + '/api/v1/targets', options);
  if (!response.ok) throw new Error('Targets API unavailable');

  const result = await response.json();
  const targets = result?.data?.activeTargets;

  if (!Array.isArray(targets)) {
    throw new Error('Invalid Prometheus targets');
  }

  const matching = targets.filter(
    target =>
      target.labels?.job === 'collector' &&
      target.scrapeUrl === 'http://collector:3001/metrics'
  );

  if (matching.length !== 1) {
    throw new Error('Collector scrape target missing or duplicated');
  }

  if (matching[0].health !== 'up') {
    throw new Error('Collector scrape is not healthy');
  }

  const metricsResponse = await fetch(
    base + '/api/v1/query?query=ft_demo_lab_present',
    options
  );

  if (!metricsResponse.ok) {
    throw new Error('Prometheus metrics query failed');
  }

  const metrics = await metricsResponse.json();
  const samples = metrics?.data?.result;

  if (
    metrics?.status !== 'success' ||
    metrics?.data?.resultType !== 'vector' ||
    !Array.isArray(samples) ||
    samples.length !== 1
  ) {
    throw new Error('Demo Lab metrics are missing');
  }

  const value = Number(samples[0]?.value?.[1]);

  if (value !== 0 && value !== 1) {
    throw new Error('Invalid Demo Lab presence metric');
  }
})().catch(() => { process.exitCode = 1; });
"""


class PrometheusBootstrapError(Exception):
    """Errors must not disclose Docker inspection details or secrets."""


class PrometheusReadinessError(PrometheusBootstrapError):
    """An HTTP/metrics probe failed after prerequisite validation."""


def container():
    return pg.exact_container(NAME)


def verify_identity(item):
    if not isinstance(item, dict):
        raise PrometheusBootstrapError("Invalid Prometheus inspect result")

    cfg = item.get("Config") or {}
    labels = cfg.get("Labels") or {}
    host = item.get("HostConfig") or {}
    networks = (item.get("NetworkSettings") or {}).get("Networks") or {}

    expected_labels = {
        "com.docker.compose.project": pg.PROJECT,
        "com.docker.compose.service": SERVICE,
        "com.docker.compose.project.config_files": str(pg.COMPOSE_FILE),
        "com.docker.compose.project.working_dir": str(pg.ROOT),
    }

    if (
        item.get("Name") != "/" + NAME
        or any(labels.get(k) != v for k, v in expected_labels.items())
        or cfg.get("Image") != IMAGE
        or cfg.get("User") != "65534:65534"
        or cfg.get("Cmd") != COMMAND
    ):
        raise PrometheusBootstrapError("Unexpected Prometheus identity")

    caps = {str(x).upper() for x in host.get("CapDrop") or []}
    security = set(host.get("SecurityOpt") or [])

    if (
        set(networks) != {NETWORK}
        or host.get("Privileged") is not False
        or host.get("PortBindings")
        or host.get("ReadonlyRootfs") is not True
        or caps != {"ALL"}
        or "no-new-privileges:true" not in security
        or "/tmp" not in (host.get("Tmpfs") or {})
    ):
        raise PrometheusBootstrapError("Unexpected Prometheus privileges or network")

    mounts = item.get("Mounts") or []
    mounts = [m for m in mounts if m.get("Type") != "tmpfs"]

    if len(mounts) != 2:
        raise PrometheusBootstrapError("Unexpected Prometheus mounts")

    by_destination = {m.get("Destination"): m for m in mounts}
    if set(by_destination) != {"/etc/prometheus", "/prometheus"}:
        raise PrometheusBootstrapError("Unexpected Prometheus mount destinations")

    config_mount = by_destination["/etc/prometheus"]
    data_mount = by_destination["/prometheus"]

    if (
        config_mount.get("Type") != "bind"
        or config_mount.get("Source") != str(CONFIG_DIR)
        or config_mount.get("RW") is not False
        or data_mount.get("Type") != "volume"
        or data_mount.get("Name") != VOLUME
        or data_mount.get("RW") is not True
    ):
        raise PrometheusBootstrapError("Unexpected Prometheus mount permissions")

    forbidden = (
        "DATABASE_URL=", "DATABASE_URL_FILE=", "VAULT_TOKEN=",
        "POSTGRES_PASSWORD=", "PGPASSWORD=",
    )
    if any(
        str(entry).startswith(forbidden)
        for entry in cfg.get("Env") or []
    ):
        raise PrometheusBootstrapError("Unexpected Prometheus credentials")

    return item


def running(item):
    state = item.get("State") or {}
    return state.get("Running") is True and state.get("Status") == "running"


def require_prerequisites():
    resources.validate_compose_contract()
    inventory = resources.scan(batch=True)

    if not all(inventory.values()):
        raise PrometheusBootstrapError(
            "Required Docker resources missing; use explicit resource preparation"
        )

    if support.main(["check"]) != 0:
        raise PrometheusBootstrapError("Trusted support services are not ready")


def probe_http():
    support.run(
        ["docker", "exec", f"{pg.PROJECT}-backend-1",
         "node", "-e", HTTP_PROBE],
        timeout=25,
    )


def wait_ready(attempts=30, delay=2):
    for attempt in range(attempts):
        item = container()
        if item is None:
            raise PrometheusBootstrapError("Prometheus container disappeared")

        verify_identity(item)
        state = item.get("State") or {}

        if running(item):
            try:
                probe_http()
                return
            except support.SupportServicesError:
                pass

        if state.get("Status") in ("dead", "exited"):
            raise PrometheusBootstrapError("Prometheus stopped unexpectedly")

        if attempt + 1 < attempts:
            time.sleep(delay)

    raise PrometheusBootstrapError("Prometheus readiness timeout")


def start_or_keep(env):
    item = container()

    if item is not None:
        verify_identity(item)
        status = (item.get("State") or {}).get("Status")

        if status == "running":
            print(f"KEEP: {NAME}", flush=True)
            return

        if status not in ("created", "exited"):
            raise PrometheusBootstrapError("Unexpected Prometheus state")

        original_id = item.get("Id")
        support.run(["docker", "start", NAME], timeout=90)

        after = container()
        if after is None or after.get("Id") != original_id:
            raise PrometheusBootstrapError("Prometheus identity changed")

        verify_identity(after)
        print(f"START: {NAME}", flush=True)
        return

    command = agent.app_compose(
        "--profile", "monitoring",
        "up", "-d", "--no-deps", "--no-recreate", SERVICE,
    )
    support.run(command, env=env, timeout=180)

    created = container()
    if created is None:
        raise PrometheusBootstrapError("Prometheus container was not created")

    verify_identity(created)
    print(f"CREATED: {NAME}", flush=True)


def apply():
    with pg.LOCK.open("rb") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise PrometheusBootstrapError(
                "Another startup or migration workflow is running"
            ) from None

        require_prerequisites()

        existing = container()
        if existing is not None:
            verify_identity(existing)

        start_or_keep(agent.safe_env())
        wait_ready()

    print("RESULT: PROMETHEUS READY (apply)", flush=True)


def check():
    """Raise typed errors while preserving every trust/readiness check."""
    item = container()

    if item is None:
        raise PrometheusBootstrapError("Prometheus container missing")

    verify_identity(item)
    require_prerequisites()

    if not running(item):
        raise PrometheusBootstrapError("Prometheus is not running")

    try:
        probe_http()
    except support.SupportServicesError:
        raise PrometheusReadinessError(
            "Prometheus HTTP/metrics probe not ready or unavailable"
        ) from None


def check_ready():
    """Bounded retries of the complete read-only check, only for HTTP readiness."""
    for attempt in range(3):
        try:
            check()
            if attempt:
                print("PASS: Prometheus HTTP readiness retry", flush=True)
            return
        except PrometheusReadinessError:
            if attempt == 2:
                raise
            print("RETRY: Prometheus HTTP readiness", flush=True)
            time.sleep(2)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("plan", "check", "check-ready", "apply"))
    args = parser.parse_args(argv)

    try:
        if args.mode == "plan":
            item = container()
            if item is None:
                print("TODO: Prometheus not created (read-only)")
            else:
                verify_identity(item)
                print(f"EXISTS: Prometheus ({(item.get('State') or {}).get('Status')})")
            return 0

        if args.mode in ("check", "check-ready"):
            (check_ready if args.mode == "check-ready" else check)()
            print("RESULT: PROMETHEUS READY (read-only)", flush=True)
            return 0

        apply()
        return 0

    except (
        PrometheusBootstrapError,
        resources.ResourceError,
        support.SupportServicesError,
        pg.PostgresBootstrapError,
        OSError,
    ) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        print(
            "RESULT: PROMETHEUS REFUSED (no resources removed)",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
