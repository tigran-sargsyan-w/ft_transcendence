#!/usr/bin/env python3
"""Read-only subprocess timing profiler for ft_transcendence development checks.

Runs only reviewed check commands and instruments Python subprocess.run in
child processes through an ephemeral sitecustomize import.  Trace records
contain durations, exit status, executable category and call-site basename;
never arguments, environment, stdio, SQL, passwords, or tokens.

This is a diagnostic tool, not part of the application's startup path.
"""

import argparse
import json
import os
from pathlib import Path
import shutil
import statistics
import subprocess
import sys
import tempfile
import time

TRACE = r'''# Ephemeral instrumentation: record process timing without any command arguments.
import json as _json
import os as _os
import subprocess as _subprocess
import sys as _sys
import time as _time

_ft_original_run = _subprocess.run
_ft_allowed_subcommands = frozenset((
    "exec", "container", "volume", "network", "compose", "start", "ps",
    "inspect", "info", "version", "run",
))

def _ft_safe_kind(args, kwargs):
    command = args[0] if args else kwargs.get("args")
    if isinstance(command, (list, tuple)) and command:
        name = _os.path.basename(command[0]) if isinstance(command[0], str) else "process"
        if name == "docker" and len(command) > 1 and command[1] in _ft_allowed_subcommands:
            return "docker " + command[1]
        # Only known, static bootstrap check names; never log arbitrary argv.
        if name == "bootstrap.sh":
            checks = frozenset((
                "--check-host", "--check-resources", "--check-vault",
                "--check-postgres", "--check-postgres-roles", "--check-migrations",
                "--check-backend-agent", "--check-backend",
                "--check-support-services", "--check-frontend", "--check-nginx",
            ))
            if len(command) > 1 and command[1] in checks:
                return "bootstrap " + command[1]
            return "bootstrap.sh"
        if name in ("docker", "bash", "sh", "python", "python3", "curl", "openssl", "make", "psql", "git"):
            return name
        return "other process"
    return "shell/process"

def _ft_run(*args, **kwargs):
    started = _time.perf_counter()
    status = "exception"
    try:
        result = _ft_original_run(*args, **kwargs)
        status = str(result.returncode)
        return result
    except _subprocess.TimeoutExpired:
        status = "timeout"
        raise
    finally:
        duration = _time.perf_counter() - started
        try:
            frame = _sys._getframe(1)
            site = _os.path.basename(frame.f_code.co_filename) + ":" + frame.f_code.co_name
            record = {"seconds": round(duration, 4), "result": status,
                      "kind": _ft_safe_kind(args, kwargs), "caller": site,
                      "pid": _os.getpid()}
            # The parent creates an owner-only directory and an empty trace file.
            # Never write subprocess arguments, stdout, stderr or env.
            fd = _os.open(_os.environ["FT_PROFILE_LOG"], _os.O_WRONLY | _os.O_APPEND | _os.O_NOFOLLOW)
            try:
                _os.write(fd, (_json.dumps(record, separators=(",", ":")) + "\n").encode())
            finally:
                _os.close(fd)
        except Exception:
            pass  # Instrumentation must never change normal subprocess semantics.

_subprocess.run = _ft_run
'''

COMMANDS = {
    "health": ["make", "health"],
    "check-all": ["make", "check-all"],
    "postgres-roles": ["./scripts/bootstrap.sh", "--check-postgres-roles"],
    "support-services": ["./scripts/bootstrap.sh", "--check-support-services"],
    "nginx": ["./scripts/bootstrap.sh", "--check-nginx"],
}


def profile_once(command, timeout, top):
    with tempfile.TemporaryDirectory(prefix="ft-unified-profile-") as temp:
        work = Path(temp)
        (work / "sitecustomize.py").write_text(TRACE, encoding="utf-8")
        log = work / "process-calls.jsonl"
        log.touch(mode=0o600)
        env = os.environ.copy()
        env["PYTHONPATH"] = str(work) + os.pathsep + env.get("PYTHONPATH", "")
        env["FT_PROFILE_LOG"] = str(log)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        started = time.perf_counter()
        status = "unknown"
        try:
            result = subprocess.run(command, env=env, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL, check=False, timeout=timeout)
            status = str(result.returncode)
        except subprocess.TimeoutExpired:
            status = "timeout"
        elapsed = time.perf_counter() - started
        records = []
        for line in log.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
                if all(field in rec for field in ("seconds", "result", "kind", "caller")):
                    records.append(rec)
            except (ValueError, TypeError):
                continue
    print(f"  Total {elapsed:.2f}s | exit={status} | traced subprocess calls={len(records)}")
    if records:
        print("  Longest subprocess calls (nested durations overlap):")
        print(f"  {'Seconds':>8}  {'Status':<9} {'Category':<19} Caller")
        for row in sorted(records, key=lambda r: r["seconds"], reverse=True)[:top]:
            print(f"  {row['seconds']:>8.2f}  {row['result']:<9} {row['kind']:<19} {row['caller']}")
        kinds = {}
        for row in records:
            kind = row["kind"]
            value = kinds.setdefault(kind, [])
            value.append(row["seconds"])
        print("  Category counts / maximum elapsed:")
        for kind, values in sorted(kinds.items(), key=lambda item: max(item[1]), reverse=True)[:8]:
            print(f"    {kind:<19} {len(values):>3} calls  max={max(values):.2f}s")
    else:
        print("  No Python subprocess calls captured; command may be shell-only.")
    if status != "0":
        print("  WARNING: check failed or timed out. Use the regular read-only check for errors.")
    return elapsed, status


def self_test():
    command = ["/usr/bin/python3" if Path("/usr/bin/python3").is_file() else sys.executable,
               "-c", "import subprocess; subprocess.run(['true'], check=True)"]
    with tempfile.TemporaryDirectory(prefix="ft-unified-selftest-") as temp:
        work = Path(temp)
        (work / "sitecustomize.py").write_text(TRACE, encoding="utf-8")
        log = work / "trace.jsonl"
        log.touch(mode=0o600)
        env = os.environ.copy()
        env["PYTHONPATH"] = str(work)
        env["FT_PROFILE_LOG"] = str(log)
        run = subprocess.run(command, env=env, check=False, capture_output=True, timeout=10)
        entries = [json.loads(x) for x in log.read_text().splitlines()]
        if run.returncode != 0 or len(entries) != 1 or entries[0]["result"] != "0":
            print("SELF TEST FAILED", file=sys.stderr)
            return 1
        print("SELF TEST PASSED: no command arguments or output stored")
        return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--command", choices=tuple(COMMANDS), default="health")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--top", type=int, default=12)
    parser.add_argument("--timeout", type=int, default=240)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test()
    if not (1 <= args.runs <= 6 and 1 <= args.top <= 30 and 10 <= args.timeout <= 600):
        parser.error("runs 1..6; top 1..30; timeout 10..600")
    if not (Path("scripts/bootstrap.sh").is_file() and Path("Makefile").is_file()):
        parser.error("Run from the ft_transcendence repository root")
    if not (shutil.which("docker") and shutil.which("make")):
        parser.error("Docker and make must be installed")
    print("=== Non-destructive process latency profiler ===")
    print("Outputs never include subprocess argv, environment or stdout/stderr.")
    times = []
    failed = False
    for i in range(args.runs):
        print(f"\nSample {i + 1}/{args.runs}: {args.command}", flush=True)
        elapsed, status = profile_once(COMMANDS[args.command], args.timeout, args.top)
        times.append(elapsed)
        if status != "0":
            failed = True
            break
    print(f"\nWall time: min={min(times):.2f}s, median={statistics.median(times):.2f}s, max={max(times):.2f}s")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
