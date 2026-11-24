#!/usr/bin/env python3
"""Issue #157: guarded, repeatable first-install wall-clock benchmark.

Run ONLY on a disposable, restored development VM. No raw command output is
stored, but the PTY still displays child output in the operator's terminal.
This tool is NOT a general-purpose unattended installer.
"""

import argparse
import json
import os
import pty
import re
import stat
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Issue #160: initialization must never request or receive approval.
STAGE_NAMES = (
    "System dependencies", "Docker images", "host", "resources", "vault",
    "vault-ui", "vault-config", "vault-secrets", "postgres", "postgres-roles",
    "migrations", "backend-agent", "backend", "support-services", "frontend",
    "nginx", "prometheus", "grafana-secrets", "grafana-agent", "grafana",
    "grafana-https",
)

TIMING = re.compile(
    r"^TIMING: stage=([a-z0-9-]+) attempt=(\d+)/(\d+) "
    r"result=(PASS|FAIL) elapsed=(\d+(?:\.\d+)?)s$"
)
# Any approval prompt is a regression, including formerly reviewed prompts.
YES_NO_PROMPT = re.compile(r"\[[yYnN]/[yYnN]\]")
STAGE_END = re.compile(
    r"^=> \[(\d+)/(\d+)\] ([A-Za-z0-9 -]+) "
    r"(DONE|CACHED|SKIPPED|FAILED|CANCELLED) (\d+(?:\.\d+)?)s \| total (\d+(?:\.\d+)?)s$"
)
PROMPT_REFUSED = "ERROR: Unexpected initialization approval prompt; stopped safely."
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
SHA = re.compile(r"[a-f0-9]{40}\Z")
LABEL = re.compile(r"[a-z0-9][a-z0-9_-]{0,31}\Z")
READY_MARKER = "RESULT: UNIFIED INITIALIZATION READY"
LIMITATIONS = (
    "Outer-stage checks only; preparation durations estimated using the next "
    "timed check; no nested Docker-command count. Image pulls and VM load "
    "influence wall-clock results."
)


class BenchmarkError(Exception):
    """A non-destructive preflight or reporting refusal."""


def git_capture(root, *args):
    try:
        result = subprocess.run(
            ["git", *args], cwd=root, capture_output=True, text=True,
            timeout=20, check=True,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        raise BenchmarkError(f"git preflight failed ({args[0]})") from exc
    return result.stdout.strip()


def ensure_empty_project_resources(listings):
    """Fail closed if any existing project-scoped Docker resource is found."""
    prefixes = ("transcendence-app-dev-", "transcendence-vault-dev-",
                "transcendence-app-dev_", "transcendence-vault-dev_")
    for kind, names in listings.items():
        for name in names:
            if name.startswith(prefixes):
                raise BenchmarkError(
                    f"existing {kind} '{name}' detected; never benchmark over a live stack"
                )


def docker_capture(root, *args):
    try:
        result = subprocess.run(
            ["docker", *args], cwd=root, capture_output=True, text=True,
            timeout=20, check=True,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        raise BenchmarkError(f"Docker preflight failed ({args[0]}); stop") from exc
    return result.stdout.splitlines()


def preflight(root, expected_commit):
    if not root.is_dir():
        raise BenchmarkError(f"repository not found: {root}")
    if not SHA.fullmatch(expected_commit):
        raise BenchmarkError("--expected-commit must be a full 40-character SHA")
    actual_root = Path(git_capture(root, "rev-parse", "--show-toplevel")).resolve()
    if actual_root != root.resolve():
        raise BenchmarkError("script is not running from its own Git repository")
    head = git_capture(root, "rev-parse", "HEAD")
    if head != expected_commit:
        raise BenchmarkError(f"commit mismatch ({head[:12]}); stop and review")
    if git_capture(root, "status", "--porcelain", "--untracked-files=all"):
        raise BenchmarkError("working tree is not clean")

    private_vault = (Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state")
                     / "transcendence/dev/private/vault-init.json")
    if private_vault.exists() or private_vault.is_symlink():
        raise BenchmarkError("existing private Vault initialization material detected")

    ensure_empty_project_resources({
        "container": docker_capture(root, "ps", "-a", "--format", "{{.Names}}"),
        "volume": docker_capture(root, "volume", "ls", "--format", "{{.Name}}"),
        "network": docker_capture(root, "network", "ls", "--format", "{{.Name}}"),
    })
    return head


class Recorder:
    """Parse reviewed stage metrics; never approve an installer prompt."""

    def __init__(self, started):
        self.started = started
        self.checks = []
        self.approvals = []  # retained in reports for baseline comparison
        self.unexpected_confirmations = 0
        self.stages = []
        self.preparations = {}
        self.line_buffer = ""
        self.last_approved_line = False
        self.rejection = None
        self.ready = False

    def process_line(self, line, now):
        clean = ANSI.sub("", line).strip()
        if clean == READY_MARKER:
            self.ready = True
        if clean == PROMPT_REFUSED:
            self.unexpected_confirmations += 1
            self.rejection = "unexpected confirmation"
        terminal = STAGE_END.fullmatch(clean)
        if terminal:
            number, count, name, status, seconds, total = terminal.groups()
            index = int(number) - 1
            if (int(count) == len(STAGE_NAMES) and index == len(self.stages)
                    and 0 <= index < len(STAGE_NAMES) and name == STAGE_NAMES[index]):
                self.stages.append({"stage": name, "status": status,
                                    "seconds": float(seconds), "total_seconds": float(total)})
        match = TIMING.fullmatch(clean)
        if match:
            stage, attempt, _, outcome, duration = match.groups()
            phase = "postcheck" if stage in self.preparations else "precheck"
            seconds = float(duration)
            self.checks.append({
                "stage": stage, "phase": phase, "attempt": int(attempt),
                "result": outcome, "seconds": seconds,
                "elapsed_seconds": round(now, 3),
            })
            info = self.preparations.get(stage)
            if phase == "postcheck" and "estimated_seconds" not in info:
                info["estimated_seconds"] = round(
                    max(0.0, now - info["started"] - seconds), 3
                )
        elif clean.startswith("PREPARE: "):
            stage = clean.removeprefix("PREPARE: ").strip()
            if re.fullmatch(r"[a-z0-9-]+", stage):
                self.preparations[stage] = {"started": now}

    def feed(self, data, reply, now=None):
        """Consume PTY bytes, rejecting every approval prompt."""
        if now is None:
            now = time.monotonic() - self.started
        decoded = data.decode("utf-8", "replace")
        for fragment in re.split(r"(\r\n|\r|\n)", decoded):
            if fragment in ("\r\n", "\r", "\n"):
                if self.line_buffer:
                    self.process_line(self.line_buffer, now)
                self.line_buffer = ""
                self.last_approved_line = False
                continue
            if not fragment:
                continue
            self.line_buffer = (self.line_buffer + fragment)[-4096:]
            match = YES_NO_PROMPT.search(ANSI.sub("", self.line_buffer))
            if not match or self.last_approved_line:
                continue
            self.last_approved_line = True
            self.unexpected_confirmations += 1
            self.rejection = "unexpected confirmation"
            reply(b"n\n")

    def successful(self, exit_code):
        return (
            exit_code == 0 and self.ready and self.rejection is None
            and self.unexpected_confirmations == 0
            and len(self.stages) == len(STAGE_NAMES)
            and all(s["status"] in ("DONE", "CACHED", "SKIPPED") for s in self.stages)
        )

    def report(self, commit, label, started_utc, elapsed, exit_code):
        return {
            "schema_version": 2,
            "label": label,
            "commit": commit,
            "started_utc": started_utc,
            "total_seconds": elapsed,
            "exit_code": exit_code,
            "successful": self.successful(exit_code),
            "readiness_marker_seen": self.ready,
            "confirmation_refusal": self.rejection is not None,
            "auto_approvals": self.approvals,
            "unexpected_confirmations": self.unexpected_confirmations,
            "stages": self.stages,
            "checks": self.checks,
            "preparations": {
                k: {key: val for key, val in v.items() if key != "started"}
                for k, v in self.preparations.items()
            },
            "limitations": LIMITATIONS,
        }


def safe_output_directory(root, requested):
    path = requested.expanduser()
    if path.is_symlink():
        raise BenchmarkError("output path must not be a symlink")
    directory = path.resolve()
    if directory.is_relative_to(root.resolve()):
        raise BenchmarkError("benchmark JSON must be written outside the repository")
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    mode = directory.stat()
    if mode.st_uid != os.getuid() or stat.S_IMODE(mode.st_mode) & 0o077:
        raise BenchmarkError("output directory must be owned by the user and private (0700)")
    return directory


def summarize(recorder, elapsed, exit_code, destination):
    print("\n=== SAFE FIRST-RUN BENCHMARK SUMMARY ===")
    print(f"Total: {elapsed:.2f}s | Exit: {exit_code} | "
          f"Unexpected confirmations: {recorder.unexpected_confirmations} (expected 0)")
    print(f"Checks measured: {len(recorder.checks)}")
    for stage in dict.fromkeys(e["stage"] for e in recorder.checks):
        entries = [e for e in recorder.checks if e["stage"] == stage]
        pre = sum(e["seconds"] for e in entries if e["phase"] == "precheck")
        post = sum(e["seconds"] for e in entries if e["phase"] == "postcheck")
        estimate = recorder.preparations.get(stage, {}).get("estimated_seconds")
        estimate_text = "—" if estimate is None else f"{estimate:.1f}s~"
        print(f"{stage:18} pre={pre:7.2f}s  prepare={estimate_text:>9} "
              f" post={post:7.2f}s  checks={len(entries)} "
              f"last={entries[-1]['result']}")
    print(f"Result: {'PASS' if recorder.successful(exit_code) else 'FAIL'}")
    if recorder.rejection:
        print("ERROR: unexpected confirmation was denied; stop and review")
    elif not recorder.ready:
        print("ERROR: unified initialization ready marker was not observed")
    elif len(recorder.stages) != len(STAGE_NAMES):
        print("ERROR: not all initialization stages were observed")
    print(f"Safe metrics JSON: {destination}")
    print("NOTE: do not share full terminal output or Vault recovery material")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-commit", required=True, help="exact full Git SHA")
    parser.add_argument("--label", default="optimized", help="baseline, optimized, etc.")
    parser.add_argument(
        "--output-dir", type=Path,
        default=Path.home() / "ft-bootstrap-benchmarks",
        help="private directory outside Git for metrics JSON",
    )
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--preflight-only", action="store_true", help="read-only checks")
    action.add_argument("--run", action="store_true", help="start installation benchmark")
    parser.add_argument(
        "--approve-disposable-vm", action="store_true",
        help="required additionally for --run; still requires a typed confirmation",
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    root = Path(__file__).resolve().parents[2]
    if not LABEL.fullmatch(args.label):
        print("ERROR: label must be 1-32 lowercase letters, numbers, '-' or '_'",
              file=sys.stderr)
        return 2
    if args.run and not args.approve_disposable_vm:
        print("ERROR: --run requires --approve-disposable-vm", file=sys.stderr)
        return 2
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        print("ERROR: a real interactive terminal is required", file=sys.stderr)
        return 2

    try:
        commit = preflight(root, args.expected_commit)
        if args.preflight_only:
            print(f"PASS: disposable-VM preflight (commit {commit[:12]})")
            print("NOTE: freshness of the VM snapshot must still be verified by the operator")
            return 0
        directory = safe_output_directory(root, args.output_dir)
    except BenchmarkError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2

    print("WARNING: this operation provisions a NEW Vault/PostgreSQL development stack.")
    print("Only run on a deliberately restored, disposable VM. Do not run on an existing stack.")
    if input("Type DISPOSABLE VM to authorize this benchmark: ") != "DISPOSABLE VM":
        print("REFUSED: operator did not authorize the benchmark")
        return 2

    os.umask(0o077)
    old_timing = os.environ.get("FT_DEV_STAGE_TIMING")
    os.environ["FT_DEV_STAGE_TIMING"] = "1"
    started = time.monotonic()
    started_utc = datetime.now(timezone.utc).isoformat()
    recorder = Recorder(started)
    print("=== FIRST-RUN BENCHMARK (zero approval prompts expected) ===", flush=True)
    print(f"Commit: {commit[:12]} | Label: {args.label} | No raw output saved", flush=True)
    try:
        def master_read(fd):
            try:
                data = os.read(fd, 4096)
            except OSError:
                return b""
            recorder.feed(data, lambda answer: os.write(fd, answer))
            return data

        status = pty.spawn(["make", "initialize"], master_read=master_read)
        exit_code = os.waitstatus_to_exitcode(status)
    finally:
        if old_timing is None:
            os.environ.pop("FT_DEV_STAGE_TIMING", None)
        else:
            os.environ["FT_DEV_STAGE_TIMING"] = old_timing

    elapsed = round(time.monotonic() - started, 3)
    filename = (f"initialize-{args.label}-"
                f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-"
                f"{commit[:12]}.json")
    destination = directory / filename
    report = recorder.report(commit, args.label, started_utc, elapsed, exit_code)
    with destination.open("x", encoding="utf-8") as output:
        json.dump(report, output, indent=2)
        output.write("\n")
    os.chmod(destination, 0o600)
    summarize(recorder, elapsed, exit_code, destination)
    return 0 if recorder.successful(exit_code) else 1


if __name__ == "__main__":
    sys.exit(main())
