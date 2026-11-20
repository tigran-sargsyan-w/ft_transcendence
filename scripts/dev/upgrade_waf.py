#!/usr/bin/env python3
"""One-time, operator-approved migration of a trusted legacy Nginx to WAF.

Usage via ./scripts/bootstrap.sh --plan-waf-upgrade or --upgrade-waf.
Never deletes volumes, provisions database/Vault, or recreates non-Nginx services.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time

import bootstrap_backend_agent as agent
import bootstrap_frontend as frontend
import bootstrap_nginx as nginx
import bootstrap_postgres as pg
import nginx_tls_access as tls

OLD_IMAGE = "nginx:1.28-alpine"
APP = pg.PROJECT
VAULT = "transcendence-vault-dev"
NAME = nginx.NAME
ROOT = pg.ROOT
COMPOSE_FILE = pg.COMPOSE_FILE
NEW_IMAGE = nginx.IMAGE

LEGACY_SERVICE = """  nginx:
    image: nginx:1.28-alpine
    ports:
      - "127.0.0.1:8080:80"
      - "127.0.0.1:8443:443"
    volumes:
      - ./infra/nginx/default.conf:/etc/nginx/conf.d/default.conf:ro
      - ./infra/nginx/certs:/etc/nginx/certs:ro
    depends_on:
      - frontend
      - backend
    restart: unless-stopped

"""

class UpgradeError(Exception):
    pass


def command(args, *, env=None, timeout=90, output=False):
    """Never print command output, which may contain sensitive configuration."""
    try:
        result = subprocess.run(args, env=env, cwd=ROOT, timeout=timeout,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise UpgradeError(f"Command unavailable or timed out: {args[0]}") from None
    if result.returncode != 0:
        raise UpgradeError(f"Command failed: {args[0]} (exit {result.returncode}; output withheld)")
    return result.stdout if output else None


def compose(*args, output=False, timeout=240):
    return command(agent.app_compose(*args), env=agent.safe_env(), timeout=timeout, output=output)


def verify_legacy(item):
    """Exact expected old Compose gateway; fail closed for unexpected identity."""
    if not isinstance(item, dict):
        raise UpgradeError("Legacy Nginx missing or inspection invalid")
    cfg = item.get("Config") or {}
    labels = cfg.get("Labels") or {}
    host = item.get("HostConfig") or {}
    if (item.get("Name") != "/" + NAME
        or cfg.get("Image") != OLD_IMAGE
        or labels.get("com.docker.compose.project") != APP
        or labels.get("com.docker.compose.service") != "nginx"
        or labels.get("com.docker.compose.project.config_files") != str(COMPOSE_FILE)
        or labels.get("com.docker.compose.project.working_dir") != str(ROOT)):
        raise UpgradeError("Unexpected legacy Nginx Compose identity")
    old_ports = {
        "80/tcp": [{"HostIp": "127.0.0.1", "HostPort": "8080"}],
        "443/tcp": [{"HostIp": "127.0.0.1", "HostPort": "8443"}],
    }
    if (host.get("Privileged") is not False
        or host.get("PortBindings") != old_ports
        or host.get("GroupAdd") not in (None, [])
        or (host.get("RestartPolicy") or {}).get("Name") != "unless-stopped"):
        raise UpgradeError("Unexpected legacy Nginx permissions, ports or restart policy")
    nets = (item.get("NetworkSettings") or {}).get("Networks") or {}
    if set(nets) != {nginx.DEFAULT_NETWORK}:
        raise UpgradeError("Unexpected legacy Nginx networks")
    mounts = item.get("Mounts") or []
    expected = {
        ("/etc/nginx/conf.d/default.conf", str(ROOT / "infra/nginx/default.conf")),
        ("/etc/nginx/certs", str(nginx.CERTS)),
    }
    if (len(mounts) != 2 or
        {(m.get("Destination"), m.get("Source")) for m in mounts} != expected or
        any(m.get("Type") != "bind" or m.get("RW") is not False for m in mounts)):
        raise UpgradeError("Unexpected legacy Nginx mounts")
    if any(str(e).startswith(nginx.FORBIDDEN_ENV) for e in cfg.get("Env") or []):
        raise UpgradeError("Unexpected secret in legacy Nginx environment")
    return item


def current_mode():
    item = nginx.container()
    if item is None:
        raise UpgradeError("Missing Nginx: use the ordinary confirmed fresh bootstrap")
    img = (item.get("Config") or {}).get("Image")
    if img == OLD_IMAGE:
        verify_legacy(item)
        mode = "legacy"
    elif img == NEW_IMAGE:
        nginx.verify_identity(item)
        mode = "waf"
    else:
        raise UpgradeError("Unknown existing Nginx image; manual review required")
    if not nginx.running(item):
        raise UpgradeError("Nginx is not running; recover existing runtime first")
    return mode


def validate_target():
    if os.environ.get("FT_APP_COMPOSE_OVERRIDE") or os.environ.get("FT_APP_COMPOSE_PROJECT") not in (None, APP):
        raise UpgradeError("Unexpected application Compose override/project")
    if COMPOSE_FILE.is_symlink() or not COMPOSE_FILE.is_file():
        raise UpgradeError("Unexpected Compose file")
    model = json.loads(compose("config", "--format", "json", output=True))
    svc = (model.get("services") or {}).get("nginx") or {}
    if (svc.get("image") != NEW_IMAGE
        or [str(v) for v in svc.get("group_add") or []] != [nginx.TLS_GROUP_GID]
        or (svc.get("environment") or {}).get("MODSEC_RULE_ENGINE") != "On"):
        raise UpgradeError("WAF Compose image/group/blocking mode mismatch")
    # Lock down the published ports and the two read-only bind mounts.
    ports = {(str(p.get("target")), str(p.get("published")), p.get("host_ip"))
             for p in svc.get("ports") or []}
    if ports != {("8080", "8080", "127.0.0.1"), ("8443", "8443", "127.0.0.1")}:
        raise UpgradeError("Unexpected WAF published ports")
    expected_mounts = {
        (str(nginx.CONFIG), "/etc/nginx/templates/conf.d/default.conf.template"),
        (str(nginx.CERTS), "/etc/nginx/certs"),
    }
    vols = svc.get("volumes") or []
    if (len(vols) != 2 or
        {(v.get("source"), v.get("target")) for v in vols} != expected_mounts or
        any(v.get("type") != "bind" or v.get("read_only") is not True for v in vols)):
        raise UpgradeError("Unexpected WAF configuration/TLS mount")
    return COMPOSE_FILE.read_bytes()


def legacy_manifest(source):
    """Swap ONLY the nginx service stanza, preserving all other Compose services."""
    text = source.decode("utf-8")
    pattern = r"(?m)^  nginx:\s*\n"
    matches = list(re.finditer(pattern, text))
    if len(matches) != 1:
        raise UpgradeError("Expected exactly one Nginx service")
    start = matches[0].start()
    remainder = text[matches[0].end():]
    match = re.search(r"(?m)^  [A-Za-z][A-Za-z0-9_-]*:\s*\n", remainder)
    if not match:
        raise UpgradeError("Cannot find next service after Nginx")
    end = matches[0].end() + match.start()
    current = text[start:end]
    if ("owasp/modsecurity-crs:" not in current or
        "group_add:" not in current or "MODSEC_RULE_ENGINE:" not in current):
        raise UpgradeError("Unexpected target Nginx service definition")
    return (text[:start] + LEGACY_SERVICE + text[end:]).encode("utf-8")


def save_atomic(path, contents, mode):
    """Replace a repo file atomically, retaining original mode, without symlinks."""
    if path.is_symlink():
        raise UpgradeError("Refusing to change symlinked Compose file")
    fd, temp = tempfile.mkstemp(prefix=".compose-waf-upgrade-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as file:
            file.write(contents)
            file.flush()
            os.fsync(file.fileno())
        os.chmod(temp, mode)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def snapshot_other_containers():
    """Read-only Docker inventory for both Compose projects."""
    out = {}
    for project in (APP, VAULT):
        ids = command(["docker", "ps", "-aq", "--filter", f"label=com.docker.compose.project={project}"],
                      output=True).decode().splitlines()
        if ids:
            items = json.loads(command(["docker", "inspect", *ids], output=True))
            for item in items:
                name = item.get("Name", "").lstrip("/")
                if not name or name in out:
                    raise UpgradeError("Unexpected Docker inventory")
                if name != NAME:
                    out[name] = item.get("Id")
    return out


def curl_status(path="/", query=None):
    url = f"https://{nginx.DOMAIN}:8443{path}"
    args = ["curl", "-q", "--silent", "--show-error", "--noproxy", "*",
            "--connect-timeout", "3", "--max-time", "12",
            "--resolve", f"{nginx.DOMAIN}:8443:127.0.0.1",
            "-o", "/dev/null", "-w", "%{http_code}"]
    if query is not None:
        args += ["-G", "--data-urlencode", query]
    return command(args + [url], output=True, timeout=18).decode().strip()


def wait_healthy(*, target=True, max_seconds=90):
    end = time.monotonic() + max_seconds
    while time.monotonic() < end:
        item = nginx.container()
        if item is None or not nginx.running(item):
            raise UpgradeError("Nginx container stopped or disappeared")
        status = ((item.get("State") or {}).get("Health") or {}).get("Status")
        if target and status == "healthy":
            nginx.verify_identity(item)
            return
        if not target:
            verify_legacy(item)
            if curl_status("/") == "200" and curl_status("/api/v1/health") == "200":
                return
        if status == "unhealthy":
            raise UpgradeError("Nginx Docker healthcheck unhealthy")
        time.sleep(2)
    raise UpgradeError("Nginx readiness timeout")


def verify_target():
    wait_healthy()
    if nginx.main(["check"]) != 0:
        raise UpgradeError("Guarded Nginx/dependency health verification failed")
    if curl_status("/") != "200" or curl_status("/api/v1/health") != "200":
        raise UpgradeError("WAF HTTPS/frontend/API probe failed")
    if curl_status("/api/v1/health", "q=1' OR '1'='1") != "403":
        raise UpgradeError("WAF CRS blocking probe failed")
    command(["docker", "exec", NAME, "sh", "-c",
             "test -r /etc/nginx/certs/key.pem && test -r /etc/nginx/certs/cert.pem"])


def read_only_checks():
    command([sys.executable, "-m", "scripts.dev.certificates", "check"], timeout=45)
    command([sys.executable, "-m", "scripts.dev.ca_trust", "check"], timeout=45)
    if frontend.main(["check"]) != 0:
        raise UpgradeError("Other services not ready; do not upgrade gateway")
    if curl_status("/") != "200" or curl_status("/api/v1/health") != "200":
        raise UpgradeError("Current HTTPS/API not ready")


def restore_legacy(target_bytes, before_ids, snapshot_dir):
    """Best-effort rollback to exact verified old service; never modify others."""
    mode = stat.S_IMODE(COMPOSE_FILE.stat().st_mode)
    legacy = legacy_manifest(target_bytes)
    (snapshot_dir / "legacy-compose.yml").write_bytes(legacy)
    try:
        save_atomic(COMPOSE_FILE, legacy, mode)
        compose("config", "--quiet")
        compose("up", "-d", "--no-deps", "--force-recreate", "nginx")
        wait_healthy(target=False)
        if snapshot_other_containers() != before_ids:
            raise UpgradeError("Other container identity changed during rollback")
        print("ROLLBACK VERIFIED: original Nginx restored; HTTPS and API working", flush=True)
    finally:
        save_atomic(COMPOSE_FILE, target_bytes, mode)
        print("NOTE: Target Compose file restored; old gateway requires rerunning --upgrade-waf before make up.",
              flush=True)


def do_upgrade(mode):
    if os.geteuid() == 0:
        raise UpgradeError("Run as ordinary project user, not root")
    # Do not allow different identities or runtime processes to race this cutover.
    with pg.LOCK.open("rb") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise UpgradeError("Another startup or migration holds the lock") from None
        target_bytes = validate_target()
        state = current_mode()
        if state == "waf":
            if tls.main(["check"]) != 0:
                raise UpgradeError("WAF TLS group/ACL check failed")
            verify_target()
            print("KEEP: Trusted WAF already installed and healthy; nothing changed", flush=True)
            return 0

        read_only_checks()
        if tls.main(["plan"]) != 0:
            raise UpgradeError("TLS group preparation unsafe; manual review required")
        legacy_manifest(target_bytes)  # Ensure rollback can be generated before changing anything.
        print("PASS: trusted legacy Nginx, healthy application and WAF target configuration", flush=True)
        if mode == "plan":
            print("PLAN ONLY: no files, host ACL or containers modified", flush=True)
            return 0
        if not sys.stdin.isatty():
            raise UpgradeError("Interactive terminal required")
        print("This operation prepares a dedicated TLS group and recreates ONLY nginx.")
        print("There will be a short HTTPS interruption. Other services and volumes are preserved.")
        if input("Type UPGRADE-WAF to proceed: ").strip() != "UPGRADE-WAF":
            raise UpgradeError("Upgrade cancelled by operator")
        # Recheck container and tracked file against pre-confirmation state.
        if current_mode() != "legacy" or COMPOSE_FILE.read_bytes() != target_bytes:
            raise UpgradeError("Gateway or Compose file changed during confirmation")
        before_ids = snapshot_other_containers()
        snapshot_dir = Path(tempfile.mkdtemp(prefix="ft-waf-upgrade-", dir="/tmp"))
        os.chmod(snapshot_dir, 0o700)
        (snapshot_dir / "target-compose.yml").write_bytes(target_bytes)
        started = False
        success = False
        try:
            print("=== Prepare dedicated WAF TLS group/ACL ===", flush=True)
            if tls.main(["apply"]) != 0 or tls.main(["check"]) != 0:
                raise UpgradeError("TLS group setup failed")
            print("=== Pull pinned WAF image ===", flush=True)
            command(["docker", "pull", NEW_IMAGE], timeout=600)
            print("=== Recreate ONLY Nginx ===", flush=True)
            started = True
            compose("up", "-d", "--no-deps", "--force-recreate", "nginx")
            print("=== Wait for WAF health and check CRS ===", flush=True)
            verify_target()
            if snapshot_other_containers() != before_ids:
                raise UpgradeError("Unexpected recreation of another Compose container")
            success = True
            print(f"PASS: WAF upgrade complete; {len(before_ids)} other containers preserved", flush=True)
        except BaseException as exc:
            if started:
                print("ERROR: WAF cutover unsuccessful; attempting Nginx-only rollback", flush=True)
                try:
                    restore_legacy(target_bytes, before_ids, snapshot_dir)
                except BaseException:
                    print("ERROR: rollback could not be verified. Do not run generic Compose commands; inspect manually.", flush=True)
            raise
        finally:
            if COMPOSE_FILE.read_bytes() != target_bytes:
                save_atomic(COMPOSE_FILE, target_bytes, stat.S_IMODE(COMPOSE_FILE.stat().st_mode))
            if success:
                shutil.rmtree(snapshot_dir)
            else:
                print(f"Recovery snapshot retained: {snapshot_dir}", flush=True)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("plan", "apply"))
    args = parser.parse_args(argv)
    try:
        return do_upgrade(args.mode)
    except (UpgradeError, nginx.NginxBootstrapError, OSError, ValueError,
            subprocess.SubprocessError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
