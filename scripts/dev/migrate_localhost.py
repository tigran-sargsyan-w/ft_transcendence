#!/usr/bin/env python3
"""One-time, explicit cutover of an existing healthy native Ubuntu deployment.

Plan is read-only. Apply builds only frontend, changes the public hostname,
recreates only backend/frontend/grafana/nginx and verifies unrelated identities.
No certificate generation, CA installation, hosts editing, migrations, or volume
removal. If an operation fails after cutover begins, stop for operator review.
"""
import argparse
from dataclasses import replace
import fcntl
import json
import os
from pathlib import Path
import platform
import stat
import subprocess
import sys
import tempfile

import bootstrap_backend as backend
import bootstrap_frontend as frontend
import bootstrap_grafana as grafana
import bootstrap_nginx as nginx
import bootstrap_postgres as pg
import bootstrap_unified as unified
from scripts.dev import certificates, configuration

OLD_DOMAIN = "transcendence.test"
TARGET_DOMAIN = "localhost"
SERVICES = ("backend", "frontend", "grafana", "nginx")
MODULES = (backend, frontend, grafana, nginx)
PROJECTS = (pg.PROJECT, "transcendence-vault-dev", "transcendence-vault-ui-dev")


class MigrationError(Exception):
    pass


def run(args, *, env=None, timeout=120):
    # Keep raw Compose/inspect output out of operator logs (potential secrets).
    result = subprocess.run(args, cwd=pg.ROOT, env=env, timeout=timeout,
                            capture_output=True, check=False)
    if result.returncode:
        raise MigrationError(f"Command failed: {args[0]} (exit {result.returncode}; output withheld)")
    return result.stdout


def compose(config, *args, timeout=120):
    env = pg.compose_env()
    env.update(config.public_environment())
    return run(pg.compose_args(*args), env=env, timeout=timeout)


def inventory():
    result = {}
    for project in PROJECTS:
        data = run(["docker", "ps", "-a", "--filter", f"label=com.docker.compose.project={project}",
                    "--no-trunc", "--format", "{{.Names}} {{.ID}}"]).decode().splitlines()
        for line in data:
            name, identity = line.split()
            if name in result:
                raise MigrationError("Duplicate container identity")
            result[name] = identity
    return result


def volume_inventory():
    result = set()
    for project in PROJECTS:
        output = run(["docker", "volume", "ls", "--filter",
                      f"label=com.docker.compose.project={project}", "--format", "{{.Name}}"])
        result.update(output.decode().splitlines())
    return result


def verify_unrelated(before, after, volumes_before, volumes_after):
    targets = {f"{pg.PROJECT}-{name}-1" for name in SERVICES}
    if {k: v for k, v in before.items() if k not in targets} != {
            k: v for k, v in after.items() if k not in targets}:
        raise MigrationError("Unexpected unrelated container identity change")
    if volumes_before != volumes_after:
        raise MigrationError("Unexpected named volume inventory change")
    if not targets.issubset(after) or any(after[name] == before.get(name) for name in targets):
        raise MigrationError("Expected four target containers to be recreated")


def read_config():
    path = configuration.CONFIG_FILE
    text, info = configuration.read_source(path)
    if info.st_uid != os.getuid():
        raise MigrationError("Public configuration must be owned by the operator")
    current = configuration.from_text(text)
    if current.domain != OLD_DOMAIN:
        raise MigrationError("This one-time migration requires the original development hostname")
    return text, info, current


def target_text(text):
    # Preserve the user's comments, other settings and values, adding final LF.
    lines = text.splitlines()
    if lines.count(f"DEV_DOMAIN={OLD_DOMAIN}") != 1:
        raise MigrationError("Expected exactly one original DEV_DOMAIN entry")
    return "\n".join(f"DEV_DOMAIN={TARGET_DOMAIN}" if line == f"DEV_DOMAIN={OLD_DOMAIN}"
                     else line for line in lines) + "\n"


def save_config(text, original, info):
    path = configuration.CONFIG_FILE
    current, actual = configuration.read_source(path)
    if (actual.st_dev, actual.st_ino, actual.st_uid, actual.st_gid, actual.st_mode, current) != (
            info.st_dev, info.st_ino, info.st_uid, info.st_gid, info.st_mode, original):
        raise MigrationError("Public configuration changed during cutover")
    fd, name = tempfile.mkstemp(prefix=".development-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            # A setgid parent can assign the temporary file a different group.
            # Do not make it group-writable unless the original group is retained.
            if info.st_mode & 0o020 and os.fstat(stream.fileno()).st_gid != info.st_gid:
                raise MigrationError("Cannot preserve the public configuration group")
            os.fchmod(stream.fileno(), stat.S_IMODE(info.st_mode))
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def verify_target_model(current, target):
    old = json.loads(compose(current, "--profile", "*", "config", "--format", "json"))
    new = json.loads(compose(target, "--profile", "*", "config", "--format", "json"))
    expected = {
        "backend": {"APP_ORIGIN": target.app_origin},
        "frontend": {"DEV_DOMAIN": target.domain},
        "grafana": {"GF_SERVER_DOMAIN": target.domain, "GF_SERVER_ROOT_URL": target.grafana_root_url},
        "nginx": {"DEV_DOMAIN": target.domain},
    }
    for service, changes in expected.items():
        old["services"][service]["environment"].update(changes)
    if old != new:
        raise MigrationError("Unexpected Compose changes outside the reviewed hostname cutover")
    print("PASS: Compose changes limited to four public service environments", flush=True)


def health():
    print("Checking development health (read-only)", flush=True)
    result = subprocess.run([str(pg.ROOT / "scripts/bootstrap.sh"), "--health-report"],
                            cwd=pg.ROOT, check=False, timeout=300)
    if result.returncode:
        raise MigrationError("Development health check failed")


def preflight(current, target):
    if os.geteuid() == 0 or platform.system() != "Linux" or "microsoft" in platform.release().lower():
        raise MigrationError("Use an ordinary user on the native Ubuntu development VM")
    if any(os.environ.get(key) for key in ("DOCKER_HOST", "DOCKER_CONTEXT", "FT_APP_COMPOSE_OVERRIDE")):
        raise MigrationError("Refusing Docker context/Compose overrides during cutover")
    endpoint = json.loads(run(["docker", "context", "inspect", "--format", "{{json .Endpoints.docker}}"]).decode())
    if endpoint.get("Host") != "unix:///var/run/docker.sock":
        raise MigrationError("Expected local Docker Unix socket")
    verify_target_model(current, target)
    root = certificates.get_ca_root()
    if root is None or not certificates.validate_certificate(root, domain=target.domain)[0]:
        raise MigrationError("Existing application TLS certificate does not cover localhost")
    tls = Path(pg.compose_env()["FT_DEV_TLS_DIR"])
    run(["openssl", "verify", "-CAfile", str(tls / "ca.crt"), "-purpose", "sslserver",
         "-verify_hostname", "localhost", str(tls / "vault.crt")])
    print("PASS: Existing application and Vault certificates cover target addresses", flush=True)
    health()
    for module in MODULES:
        item = module.container()
        module.verify_identity(item)
        if not item.get("State", {}).get("Running"):
            raise MigrationError("All four target containers must already be running")
        if module is not frontend:
            # A mutable local tag must not silently upgrade backend or operators.
            image = item["Config"]["Image"]
            actual = run(["docker", "image", "inspect", "--format", "{{.Id}}", image]).decode().strip()
            if actual != item.get("Image"):
                raise MigrationError("A non-frontend image tag differs from the running container")
    print("PASS: Trusted target container identities", flush=True)


def verify_runtime_environment(target):
    expected = {
        "backend": {"APP_ORIGIN": target.app_origin,
                    "COOKIE_SECURE": "true", "SESSION_TTL_HOURS": str(target.session_ttl_hours)},
        "frontend": {"DEV_DOMAIN": target.domain},
        "grafana": {"GF_SERVER_DOMAIN": target.domain, "GF_SERVER_ROOT_URL": target.grafana_root_url},
        "nginx": {"DEV_DOMAIN": target.domain, "DEV_HTTPS_PORT": str(target.https_port)},
    }
    for service, values in expected.items():
        item = pg.exact_container(f"{pg.PROJECT}-{service}-1")
        if not item or not item.get("State", {}).get("Running"):
            raise MigrationError("Recreated target is not running")
        entries = item.get("Config", {}).get("Env", [])
        for key, value in values.items():
            if [entry for entry in entries if entry.startswith(key + "=")] != [f"{key}={value}"]:
                raise MigrationError("Unexpected recreated target public environment: " + service)
    print("PASS: Actual public runtime environments", flush=True)


def migrate(apply):
    original, info, current = read_config()
    target = replace(current, domain=TARGET_DOMAIN)
    updated = target_text(original)
    preflight(current, target)
    print(f"Application: {current.app_origin} -> {target.app_origin}")
    print("Recreate only: " + ", ".join(SERVICES))
    print("Preserve: all other containers, named volumes, TLS certificates, Vault state")
    if not apply:
        print("RESULT: LOCALHOST CUTOVER PLAN READY (read-only)", flush=True)
        return
    before, volumes = inventory(), volume_inventory()
    # Build only the frontend; backend code is unchanged and existing images are used.
    print("Building frontend image before changing the hostname", flush=True)
    compose(current, "build", "frontend", timeout=900)
    save_config(updated, original, info)
    print("Public hostname changed. Recreating reviewed services only", flush=True)
    compose(target, "up", "-d", "--no-deps", "--force-recreate", "--no-build", "--pull", "never",
            "--wait", "--wait-timeout", "180", "backend", "frontend", "grafana", timeout=300)
    compose(target, "up", "-d", "--no-deps", "--force-recreate", "--no-build", "--pull", "never",
            "--wait", "--wait-timeout", "180", "nginx", timeout=300)
    verify_unrelated(before, inventory(), volumes, volume_inventory())
    verify_runtime_environment(target)
    health()
    print("RESULT: LOCALHOST CUTOVER HEALTHY", flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("plan", "apply"))
    args = parser.parse_args(argv)
    try:
        if args.mode == "plan":
            migrate(False)
        else:
            with unified.exclusive_orchestrator_lock(), pg.LOCK.open("rb") as lock:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                migrate(True)
        return 0
    except (MigrationError, configuration.ConfigurationError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
    except Exception:
        print("ERROR: Cutover precondition or operation failed (details withheld)", file=sys.stderr)
    print("RESULT: LOCALHOST CUTOVER STOPPED; inspect configuration and target containers; no automatic rollback", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
