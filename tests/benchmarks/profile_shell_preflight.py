#!/usr/bin/env python3
"""Read-only diagnostic for time hidden in Bash prerequisites and Docker CLI.

The profiler adds temporary, owner-only PATH wrappers for a fixed list of
programs. Wrappers forward stdin/stdout/stderr and argv UNMODIFIED, and record
only an allowlisted program name, PID, exit status, and monotonic-ish wall
clock timestamps. Nothing is written inside the repository or Docker volumes.

Check commands create only the pre-existing bootstrap's --rm helper containers.
Unlike the Python subprocess profiler, this also observes commands run by Bash.
"""

import argparse
from collections import defaultdict
import os
from pathlib import Path
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
import shlex

ROOT = Path(__file__).resolve().parents[2]
COMMANDS = {
    "health": ("make", "health"),
    "check-all": ("make", "check-all"),
}
# All names must be fixed. Never classify by untrusted argument content.
WATCH = ("make", "python3", "docker", "dpkg-query", "git", "openssl", "curl", "id")
# Fixed, non-sensitive classifications; never store or print argument values.
DOCKER_CLASSES = frozenset((
    "docker.compose.vault-config", "docker.compose.other",
    "docker.run.vault-secrets", "docker.run.other",
    "docker.exec.vault", "docker.exec.postgres", "docker.exec.backend",
    "docker.exec.nginx", "docker.exec.other", "docker.container",
    "docker.volume", "docker.network", "docker.ps", "docker.info",
    "docker.buildx", "docker.other",
))
SAFE_LABELS = frozenset(WATCH) | DOCKER_CLASSES

# Only the exact known container or script names influence a fixed category.
# The original command and every argument are passed through unchanged.
DOCKER_CLASSIFIER = r'''if [ "$category" = docker ]; then
    case "${1:-}" in
        compose)
            category=docker.compose.other
            for ft_arg do
                case "$ft_arg" in
                    vault-bootstrap) category=docker.compose.vault-config; break ;;
                esac
            done
            ;;
        run)
            category=docker.run.other
            for ft_arg do
                case "$ft_arg" in
                    /bootstrap/helper.py) category=docker.run.vault-secrets; break ;;
                esac
            done
            ;;
        exec)
            category=docker.exec.other
            for ft_arg do
                case "$ft_arg" in
                    transcendence-vault-dev-vault-1) category=docker.exec.vault; break ;;
                    transcendence-app-dev-postgres-1) category=docker.exec.postgres; break ;;
                    transcendence-app-dev-backend-1) category=docker.exec.backend; break ;;
                    transcendence-app-dev-nginx-1) category=docker.exec.nginx; break ;;
                esac
            done
            ;;
        container|volume|network|ps|info|buildx)
            category="docker.$1" ;;
        *) category=docker.other ;;
    esac
fi
'''


def wrapper_source(label, binary, log):
    # The child receives the original command and unchanged argv/stdin/stdout.
    # Never write arbitrary arguments, Docker details, or subprocess output.
    return ("#!/bin/sh\n"
            f"category={shlex.quote(label)}\n"
            + DOCKER_CLASSIFIER
            + "start=$(/usr/bin/date +%s%N)\n"
            f"printf 'B\\t%s\\t%s\\t%s\\n' \"$category\" \"$$\" \"$start\" >> {shlex.quote(str(log))}\n"
            f"{shlex.quote(str(binary))} \"$@\"\n"
            "status=$?\n"
            "end=$(/usr/bin/date +%s%N)\n"
            f"printf 'E\\t%s\\t%s\\t%s\\t%s\\n' \"$category\" \"$$\" \"$end\" \"$status\" >> {shlex.quote(str(log))}\n"
            "exit \"$status\"\n")


def parse_records(contents):
    """Parse only the fixed, credential-free wire format."""
    started = defaultdict(list)
    calls = []
    for line in contents.splitlines():
        parts = line.split("\t")
        if not parts or parts[0] not in ("B", "E"):
            continue
        try:
            if parts[0] == "B" and len(parts) == 4:
                _, name, pid, stamp = parts
                if name in SAFE_LABELS:
                    started[(name, int(pid))].append(int(stamp))
            elif parts[0] == "E" and len(parts) == 5:
                _, name, pid, stamp, status = parts
                if name not in SAFE_LABELS:
                    continue
                key = (name, int(pid))
                if started[key]:
                    begin = started[key].pop()
                    duration = (int(stamp) - begin) / 1_000_000_000
                    if duration >= 0:
                        calls.append((duration, name, int(status)))
        except (TypeError, ValueError):
            continue
    outstanding = sum(map(len, started.values()))
    return calls, outstanding


def profile_once(command, timeout, top):
    with tempfile.TemporaryDirectory(prefix="ft-shell-prof-") as temp:
        work = Path(temp)
        work.chmod(0o700)
        bin_dir = work / "bin"
        bin_dir.mkdir(mode=0o700)
        log = work / "events.tsv"
        log.touch(mode=0o600)
        for name in WATCH:
            resolved = shutil.which(name)
            if not resolved:
                raise RuntimeError(f"Required executable unavailable: {name}")
            target = bin_dir / name
            target.write_text(wrapper_source(name, Path(resolved).resolve(), log), encoding="utf-8")
            target.chmod(0o700)
        env = os.environ.copy()
        env["PATH"] = str(bin_dir) + os.pathsep + env.get("PATH", "")
        # The existing orchestrator prints only safe stage names, result, time.
        env["FT_DEV_STAGE_TIMING"] = "1"
        # Capture only in memory, then display allowlisted stage timing lines.
        # Neither helper stdout nor stderr is ever written into a trace file.
        start = time.monotonic()
        try:
            result = subprocess.run(command, cwd=ROOT, env=env,
                                    stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                    check=False, timeout=timeout)
            status = str(result.returncode)
            output = result.stdout
        except subprocess.TimeoutExpired:
            status = "timeout"
            output = b""
        elapsed = time.monotonic() - start
        calls, outstanding = parse_records(log.read_text(encoding="utf-8"))
        stage_lines = [line for line in output.decode("utf-8", errors="replace").splitlines()
                       if line.startswith("TIMING: stage=") and len(line) < 180]
    print(f"  Wall={elapsed:.2f}s status={status} calls={len(calls)} incomplete={outstanding}")
    for line in stage_lines:
        print("  " + line)
    print("  Longest measured PATH calls (nested durations overlap):")
    for seconds, name, code in sorted(calls, reverse=True)[:top]:
        print(f"    {seconds:8.2f}s  exit={code:<3} {name}")
    if not calls:
        print("  No PATH calls were captured")
    return elapsed, status


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--command", choices=COMMANDS, default="health")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--top", type=int, default=12)
    parser.add_argument("--timeout", type=int, default=240)
    args = parser.parse_args(argv)
    if not 1 <= args.runs <= 6 or not 1 <= args.top <= 30 or not 10 <= args.timeout <= 600:
        parser.error("runs 1..6; top 1..30; timeout 10..600")
    if not (Path.cwd() / "scripts/bootstrap.sh").is_file() or not (ROOT / "Makefile").is_file():
        parser.error("Run from the repository root")
    if not Path("/usr/bin/date").is_file():
        parser.error("/usr/bin/date is required")
    print("=== Shell and Docker CLI preflight profiler (non-provisioning) ===", flush=True)
    print("Never logs command arguments, environment, stdout, stderr, or secrets.", flush=True)
    times = []
    for index in range(args.runs):
        print(f"\nSample {index + 1}/{args.runs}: {args.command}", flush=True)
        try:
            elapsed, status = profile_once(COMMANDS[args.command], args.timeout, args.top)
        except (OSError, RuntimeError) as exc:
            # Only allowed error string; never raw helper output.
            print(f"STOP: profiler unavailable ({type(exc).__name__})", file=sys.stderr)
            return 1
        times.append(elapsed)
        if status != "0":
            print("STOP: check failed. No automatic remediation attempted.")
            return 1
    print(f"\nWall: min={min(times):.2f}s median={statistics.median(times):.2f}s max={max(times):.2f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
