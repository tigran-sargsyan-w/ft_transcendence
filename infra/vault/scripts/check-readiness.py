#!/usr/bin/env python3
"""Read-only Vault-backed development stack readiness check (issue #106)."""

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def execute(args, timeout=12):
    """Run read-only probes; never print subprocess output directly."""
    try:
        return subprocess.run(
            args, capture_output=True, text=True, check=False, timeout=timeout
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


class Report:
    def __init__(self):
        self.failed = False

    def show(self, level, component, detail):
        print(f"{level:<4} {component}: {detail}")
        if level == "FAIL":
            self.failed = True

    def container(self, project, service, label, health_required):
        result = execute([
            "docker", "ps", "--all", "--quiet",
            "--filter", f"label=com.docker.compose.project={project}",
            "--filter", f"label=com.docker.compose.service={service}",
        ])
        if result is None or result.returncode != 0:
            self.show("FAIL", label, "unable to query Docker")
            return None

        ids = result.stdout.split()
        if len(ids) != 1:
            self.show("FAIL", label, f"expected exactly one container, found {len(ids)}")
            return None

        result = execute([
            "docker", "inspect", "--format",
            "{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}",
            ids[0],
        ])
        if result is None or result.returncode != 0:
            self.show("FAIL", label, "unable to inspect container")
            return None

        parts = result.stdout.strip().split("|")
        if len(parts) != 2:
            self.show("FAIL", label, "unexpected Docker status response")
            return None

        status, health = parts
        if status != "running":
            self.show("FAIL", label, f"container is {status}")
            return None
        if health_required and health != "healthy":
            self.show("FAIL", label, f"container running, health={health}")
            return ids[0]
        if health == "unhealthy":
            self.show("FAIL", label, "container running, health=unhealthy")
        elif health == "starting" and health_required:
            self.show("FAIL", label, "container health is still starting")
        elif health == "none":
            self.show("PASS", label, "running (no healthcheck defined)")
        else:
            self.show("PASS", label, f"running, health={health}")
        return ids[0]

    def vault_status(self, container_id):
        result = execute(["docker", "exec", container_id, "vault", "status", "-format=json"])
        if result is None or result.returncode not in (0, 2):
            self.show("FAIL", "Vault API", "unable to obtain Vault status")
            return
        try:
            info = json.loads(result.stdout)
            initialized = info["initialized"]
            sealed = info["sealed"]
            if not isinstance(initialized, bool) or not isinstance(sealed, bool):
                raise ValueError("invalid state types")
        except (ValueError, KeyError, TypeError):
            self.show("FAIL", "Vault API", "invalid Vault status response")
            return
        if not initialized:
            self.show("FAIL", "Vault API", "not initialized; operator bootstrap required")
        elif sealed:
            self.show("FAIL", "Vault API", "sealed; manual unseal required")
        else:
            self.show("PASS", "Vault API", "initialized and unsealed")

    def tls(self, tls_dir):
        cert, ca = tls_dir / "vault.crt", tls_dir / "ca.crt"
        for path in (cert, ca):
            try:
                valid = not path.is_symlink() and path.is_file() and path.stat().st_size > 0
            except OSError:
                valid = False
            if not valid:
                self.show("FAIL", "Vault TLS", f"missing or invalid {path.name}")
                return
        verify = execute([
            "openssl", "verify", "-purpose", "sslserver", "-verify_hostname", "vault",
            "-CAfile", str(ca), str(cert),
        ])
        if verify is None or verify.returncode != 0:
            self.show("FAIL", "Vault TLS", "certificate chain, hostname, or validity check failed")
            return
        expires = execute(["openssl", "x509", "-in", str(cert), "-noout", "-enddate"])
        remaining = execute(["openssl", "x509", "-in", str(cert), "-noout", "-checkend", "604800"])
        if expires is None or expires.returncode != 0 or remaining is None:
            self.show("FAIL", "Vault TLS", "could not inspect certificate expiration")
            return
        expiry = expires.stdout.strip().removeprefix("notAfter=")
        if remaining.returncode != 0:
            self.show("FAIL", "Vault TLS", f"expires within 7 days: {expiry}")
        else:
            self.show("PASS", "Vault TLS", f"local certificate valid, expires {expiry}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("vault", "stack"), default="stack",
                        help="vault: pre-start Vault checks; stack: include running services")
    parser.add_argument("--vault-project", default="transcendence-vault-poc")
    parser.add_argument("--app-project", default="transcendence")
    parser.add_argument("--tls-dir", type=Path, default=ROOT / "infra/vault/server/tls")
    args = parser.parse_args(argv)

    report = Report()
    report.tls(args.tls_dir)
    vault = report.container(args.vault_project, "vault", "Vault Server", False)
    if vault is not None:
        report.vault_status(vault)
    else:
        report.show("SKIP", "Vault API", "server is not running or could not be identified")
    if args.mode == "stack":
        report.container(args.app_project, "vault-backend-agent", "Runtime Agent", True)
        report.container(args.app_project, "postgres", "PostgreSQL", True)
        report.container(args.app_project, "backend", "Backend", True)
    report.show("INFO", "SecretID", "expiry/remaining uses not checked; no privileged token read")
    print("RESULT: " + ("NOT READY" if report.failed else "READY"))
    return 1 if report.failed else 0


if __name__ == "__main__":
    sys.exit(main())
