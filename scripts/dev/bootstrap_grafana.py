#!/usr/bin/env python3
"""Trusted Grafana startup without recreation or credential rotation."""

if __package__:
    from . import bootstrap_plan_events as plan_events
else:
    import bootstrap_plan_events as plan_events

import argparse
import fcntl
import json
import sys
import time

import bootstrap_backend_agent as agent
import bootstrap_grafana_agent as grafana_agent
import bootstrap_postgres as pg
import bootstrap_prometheus as prometheus
import bootstrap_resources as resources

SERVICE = "grafana"
NAME = f"{pg.PROJECT}-{SERVICE}-1"
IMAGE = "grafana/grafana:13.3.1"
NETWORK = f"{pg.PROJECT}_default"
DATA_VOLUME = f"{pg.PROJECT}_grafana_data"
SECRET_VOLUME = f"{pg.PROJECT}_grafana_vault_rendered"
CONFIG_DIR = pg.ROOT / "infra/monitoring/grafana"

ENTRYPOINT = ["/bin/sh", "-ec"]

STARTUP = """for secret in admin_password secret_key; do
  path="/run/secrets/grafana/$secret"

  if ! test -s "$path" || ! test -r "$path"; then
    echo "Required Grafana secret unavailable" >&2
    exit 1
  fi

  if ! grep -Eq '^[0-9a-f]{64}$' "$path"; then
    echo "Invalid Grafana secret format" >&2
    exit 1
  fi
done

exec /run.sh"""

EXPECTED_MOUNTS = {
    "/etc/grafana/grafana.ini": (
        "bind", str(CONFIG_DIR / "grafana.ini"), False,
    ),
    "/etc/grafana/provisioning/datasources": (
        "bind", str(CONFIG_DIR / "provisioning/datasources"), False,
    ),
    "/etc/grafana/provisioning/dashboards": (
        "bind", str(CONFIG_DIR / "provisioning/dashboards"), False,
    ),
    "/etc/grafana/dashboards": (
        "bind", str(CONFIG_DIR / "dashboards"), False,
    ),
    "/var/lib/grafana": (
        "volume", DATA_VOLUME, True,
    ),
    "/run/secrets/grafana": (
        "volume", SECRET_VOLUME, False,
    ),
}

GRAFANA_PUBLIC = pg.configuration.load()
GRAFANA_ENV = {
    "GF_SERVER_DOMAIN": GRAFANA_PUBLIC.domain,
    "GF_SERVER_ROOT_URL": GRAFANA_PUBLIC.grafana_root_url,
    "GF_PATHS_CONFIG": "/etc/grafana/grafana.ini",
    "GF_PATHS_DATA": "/var/lib/grafana",
    "GF_PATHS_HOME": "/usr/share/grafana",
    "GF_PATHS_LOGS": "/var/lib/grafana/log",
    "GF_PATHS_PLUGINS": "/var/lib/grafana/plugins",
    "GF_PATHS_PROVISIONING": "/etc/grafana/provisioning",
}

BASE_URL = "http://127.0.0.1:3000/grafana"

# Read the password only inside Grafana. Supply HTTP authentication to
# curl through stdin, not a command-line argument or exported variable.

AUTH_PROBE = r"""
set -eu

password="$(cat /run/secrets/grafana/admin_password)"

[ "${#password}" -eq 64 ]

case "$password" in
    *[!0-9a-f]*) exit 1 ;;
esac

printf 'user = "admin:%s"\n' "$password" |
    curl -q --config - --fail --silent --show-error \
        --noproxy '*' --max-time 5 \
        "http://127.0.0.1:3000/grafana$1"

unset password
"""


class GrafanaBootstrapError(Exception):
    """Safe diagnostics: never include credentials or raw API responses."""


def container():
    return pg.exact_container(NAME)


def verify_identity(item):
    if not isinstance(item, dict):
        raise GrafanaBootstrapError("Grafana container missing")

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

    command = cfg.get("Cmd") or []

    if (
        item.get("Name") != "/" + NAME
        or any(labels.get(k) != v for k, v in expected_labels.items())
        or cfg.get("Image") != IMAGE
        or cfg.get("User") != "472:20030"
        or cfg.get("Entrypoint") != ENTRYPOINT
        or len(command) != 1
        or not isinstance(command[0], str)
        or command[0].strip() != STARTUP
    ):
        raise GrafanaBootstrapError("Unexpected Grafana identity")

    if (
        set(networks) != {NETWORK}
        or host.get("Privileged") is not False
        or host.get("PortBindings")
        or host.get("PublishAllPorts")
        or host.get("ReadonlyRootfs") is not True
        or set(host.get("CapDrop") or []) != {"ALL"}
        or host.get("CapAdd")
        or host.get("Devices")
        or "no-new-privileges:true" not in (host.get("SecurityOpt") or [])
        or set(host.get("Tmpfs") or {}) != {"/tmp"}
    ):
        raise GrafanaBootstrapError("Unexpected Grafana runtime privileges")

    mounts = [
        mount for mount in item.get("Mounts", [])
        if mount.get("Type") != "tmpfs"
    ]

    if len(mounts) != len(EXPECTED_MOUNTS):
        raise GrafanaBootstrapError("Unexpected Grafana mount count")

    actual = {}

    for mount in mounts:
        kind = mount.get("Type")
        source = mount.get("Name") if kind == "volume" else mount.get("Source")

        actual[mount.get("Destination")] = (
            kind, source, mount.get("RW"),
        )

    if actual != EXPECTED_MOUNTS:
        raise GrafanaBootstrapError("Unexpected Grafana mounts")

    environment = {}

    for entry in cfg.get("Env") or []:
        key, separator, value = entry.partition("=")

        if not separator or key in environment:
            raise GrafanaBootstrapError("Unexpected Grafana environment")

        environment[key] = value

    if environment.get("GF_PATHS_LOGS") != GRAFANA_ENV["GF_PATHS_LOGS"]:
        raise GrafanaBootstrapError("Unexpected Grafana log path")

    for key, value in environment.items():
        if key.startswith("GF_") and GRAFANA_ENV.get(key) != value:
            raise GrafanaBootstrapError("Unexpected Grafana environment override")

        if key in (
            "VAULT_TOKEN", "DATABASE_URL", "DATABASE_URL_FILE",
            "POSTGRES_PASSWORD", "PGPASSWORD",
        ):
            raise GrafanaBootstrapError("Unexpected application credentials")

    return item


def running(item):
    state = item.get("State") or {}

    return (
        state.get("Running") is True
        and state.get("Status") == "running"
    )



PROMETHEUS_CHECK_ATTEMPTS = 3
PROMETHEUS_CHECK_DELAY = 2


def require_prerequisites():
    print("STEP: Verify Grafana prerequisites", flush=True)
    resources.validate_compose_contract()

    if not all(resources.scan(batch=True).values()):
        raise GrafanaBootstrapError("Required Docker resources missing")

    if grafana_agent.main(["check"]) != 0:
        raise GrafanaBootstrapError("Grafana Vault Agent is not ready")

    # Retry only the complete read-only Prometheus verification.
    # Never accept an unsuccessful probe or bypass trust checks.
    for attempt in range(PROMETHEUS_CHECK_ATTEMPTS):
        try:
            prometheus.check()
        except prometheus.PrometheusReadinessError:
            if attempt + 1 == PROMETHEUS_CHECK_ATTEMPTS:
                raise GrafanaBootstrapError(
                    "Prometheus HTTP/metrics readiness failed after bounded checks"
                ) from None
            time.sleep(PROMETHEUS_CHECK_DELAY)
        else:
            break
    print("PASS: Grafana prerequisites verified", flush=True)


def request_json(command):
    result = agent.run(command, timeout=15)

    try:
        data = json.loads(result.stdout)

        if not isinstance(data, dict):
            raise ValueError

        return data

    except (TypeError, ValueError, UnicodeError):
        raise GrafanaBootstrapError("Unexpected Grafana API response") from None


def probe_health():
    print("STEP: Verify Grafana HTTP health", flush=True)
    result = request_json([
        "docker", "exec", NAME,
        "curl", "-q", "--fail", "--silent", "--show-error",
        "--noproxy", "*", "--max-time", "5",
        BASE_URL + "/api/health",
    ])

    if (
        result.get("database") != "ok"
        or result.get("version") != "13.3.1"
    ):
        raise GrafanaBootstrapError("Grafana database or version not ready")
    print("PASS: Grafana HTTP health", flush=True)


def authenticated_api(path):
    if path not in (
        "/api/user",
        "/api/datasources/uid/ft-prometheus",
        "/api/dashboards/uid/ft-demo-lab",
        "/api/dashboards/uid/ft-monitoring-pipeline",
    ):
        raise GrafanaBootstrapError("Unexpected Grafana API path")

    return request_json([
        "docker", "exec", NAME,
        "/bin/sh", "-ec", AUTH_PROBE,
        "grafana-probe", path,
    ])


def verify_provisioning():
    print("STEP: Verify Grafana authentication and provisioning", flush=True)
    user = authenticated_api("/api/user")

    if user.get("login") != "admin":
        raise GrafanaBootstrapError("Grafana authentication not verified")

    datasource = authenticated_api("/api/datasources/uid/ft-prometheus")

    expected = {
        "uid": "ft-prometheus",
        "type": "prometheus",
        "url": "http://prometheus:9090",
        "access": "proxy",
        "isDefault": True,
        "readOnly": True,
    }

    if any(datasource.get(k) != v for k, v in expected.items()):
        raise GrafanaBootstrapError("Prometheus datasource mismatch")

    for uid, count in (
        ("ft-demo-lab", 5),
        ("ft-monitoring-pipeline", 6),
    ):
        data = authenticated_api("/api/dashboards/uid/" + uid)
        dashboard = data.get("dashboard") or {}
        metadata = data.get("meta") or {}

        if (
            dashboard.get("uid") != uid
            or len(dashboard.get("panels") or []) != count
            or metadata.get("provisioned") is not True
        ):
            raise GrafanaBootstrapError(f"Grafana dashboard mismatch: {uid}")

    print("PASS: Grafana authentication and provisioning", flush=True)


def verify_anonymous_access():
    result = agent.run([
        "docker", "exec", NAME,
        "curl", "-q", "--silent", "--show-error",
        "--noproxy", "*", "--max-time", "5",
        "--output", "/dev/null",
        "--write-out", "%{http_code}",
        BASE_URL + "/api/user",
    ], timeout=15)

    if result.stdout.strip() != b"401":
        raise GrafanaBootstrapError("Anonymous access rejection not verified")

    print("PASS: Grafana anonymous API access rejected", flush=True)


def probe_http():
    probe_health()
    verify_provisioning()
    verify_anonymous_access()


def start_or_keep(env):
    item = container()

    if item is not None:
        verify_identity(item)
        status = (item.get("State") or {}).get("Status")

        if running(item):
            print(f"KEEP: {NAME}", flush=True)
            return

        if status not in ("created", "exited"):
            raise GrafanaBootstrapError("Unexpected Grafana state")

        original_id = item.get("Id")

        if not original_id:
            raise GrafanaBootstrapError("Grafana container ID unavailable")

        agent.run(["docker", "start", original_id], timeout=90)

        after = verify_identity(container())

        if after.get("Id") != original_id:
            raise GrafanaBootstrapError("Grafana container identity changed")

        print(f"START: {NAME}", flush=True)
        return

    agent.run(agent.app_compose(
        "--profile", "monitoring",
        "up", "-d", "--no-deps", "--no-recreate",
        "--no-build", "--pull", "never",
        SERVICE,
    ), env=env, timeout=180)

    verify_identity(container())

    print(f"CREATED: {NAME}", flush=True)


def wait_ready(attempts=60, delay=2):
    for attempt in range(attempts):
        item = verify_identity(container())

        if running(item):
            try:
                probe_health()
            except (GrafanaBootstrapError, agent.BackendAgentError):
                pass
            else:
                verify_provisioning()
                verify_anonymous_access()
                return

        if (item.get("State") or {}).get("Status") in ("dead", "exited"):
            raise GrafanaBootstrapError("Grafana stopped unexpectedly")

        if attempt + 1 < attempts:
            time.sleep(delay)

    raise GrafanaBootstrapError("Grafana readiness timeout")


def check():
    print("STEP: Verify Grafana container identity", flush=True)
    item = verify_identity(container())
    print("PASS: Grafana container identity and privileges", flush=True)
    require_prerequisites()

    if not running(item):
        raise GrafanaBootstrapError("Grafana is not running")

    probe_http()


def apply():
    with pg.LOCK.open("rb") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise GrafanaBootstrapError(
                "Another provisioning workflow is running"
            ) from None

        require_prerequisites()

        existing = container()

        if existing is not None:
            verify_identity(existing)

        start_or_keep(agent.safe_env())
        wait_ready()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("plan", "check", "apply"))
    args = parser.parse_args(argv)

    try:
        if args.mode == "plan":
            item = container()

            if item is None:
                plan_events.container("Grafana", item)
                print("TODO: Grafana not created (read-only)")
            else:
                verify_identity(item)
                plan_events.container("Grafana", item)
                print("EXISTS: Trusted Grafana container")

            return 0

        if args.mode == "check":
            check()
        else:
            apply()

        print(f"RESULT: GRAFANA READY ({args.mode})", flush=True)
        return 0

    except (
        GrafanaBootstrapError,
        prometheus.PrometheusBootstrapError,
        agent.BackendAgentError,
        resources.ResourceError,
        pg.PostgresBootstrapError,
        OSError,
    ) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        print(
            "RESULT: GRAFANA STOPPED (no resources removed)",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
