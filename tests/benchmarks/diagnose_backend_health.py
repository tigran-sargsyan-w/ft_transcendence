#!/usr/bin/env python3
"""Non-mutating diagnostic: distinguish Docker exec/Node stalls from HTTP latency.

For an already healthy ft_transcendence development VM. Does not modify
containers, run migrations, print subprocess output, or read secret files.
"""

import argparse
from pathlib import Path
import statistics
import subprocess
import sys
import time

NAME = "transcendence-app-dev-backend-1"
NODE_CHECK = r"""
(async () => {
  const res = await fetch('http://127.0.0.1:3000/api/v1/health', {
    signal: AbortSignal.timeout(5000),
  });
  if (res.status !== 200) { process.exitCode = 1; return; }
  const body = await res.json();
  if (body?.data?.status !== 'ok' || body?.data?.database !== 'ok')
    process.exitCode = 1;
})().catch(() => { process.exitCode = 1; });
"""

# All argv values are fixed and contain no user-supplied secrets or files.
CASES = (
    ("https-nginx", [
        "curl", "-q", "--silent", "--show-error", "--noproxy", "*",
        "--connect-timeout", "3", "--max-time", "8",
        "--resolve", "transcendence.test:8443:127.0.0.1",
        "--output", "/dev/null", "--write-out", "%{http_code}",
        "https://transcendence.test:8443/api/v1/health",
    ]),
    ("docker-exec-node", ["docker", "exec", NAME, "node", "-e", "process.exit(0)"]),
    ("docker-exec-health", ["docker", "exec", NAME, "node", "-e", NODE_CHECK]),
)


def probe(name, argv, timeout):
    started = time.perf_counter()
    try:
        result = subprocess.run(
            argv, check=False, timeout=timeout,
            stdout=subprocess.PIPE if name == "https-nginx" else subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        code = result.returncode
        if name == "https-nginx" and code == 0 and result.stdout != b"200":
            status = "HTTP_NOT_200"
        else:
            status = "PASS" if code == 0 else f"EXIT_{code}"
    except subprocess.TimeoutExpired:
        status = "TIMEOUT"
    except OSError:
        status = "UNAVAILABLE"
    return time.perf_counter() - started, status


def inspect_backend(timeout):
    # Only the expected status fields, never full Docker inspect metadata.
    command = [
        "docker", "inspect", "--format",
        "{{.State.Running}} {{if .State.Health}}{{.State.Health.Status}}{{end}}",
        NAME,
    ]
    try:
        response = subprocess.run(
            command, capture_output=True, check=False, timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return response.returncode == 0 and response.stdout.strip() == b"true healthy"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=8)
    parser.add_argument("--timeout", type=int, default=12)
    args = parser.parse_args(argv)
    if not (1 <= args.runs <= 30 and 8 <= args.timeout <= 30):
        parser.error("--runs 1..30; --timeout 8..30 seconds")
    if not (Path("scripts/bootstrap.sh").is_file() and Path("compose.yml").is_file()):
        parser.error("Run from ft_transcendence repository root")
    if not inspect_backend(args.timeout):
        print("ERROR: expected backend container not healthy; no probes run", file=sys.stderr)
        return 1

    print("=== Backend health latency diagnostic (read-only) ===", flush=True)
    print("No Docker inspect details, HTTP bodies, command output, or secrets displayed.", flush=True)
    results = {name: [] for name, _ in CASES}
    failed = False
    for index in range(1, args.runs + 1):
        print(f"\nRound {index}/{args.runs}", flush=True)
        for name, command in CASES:
            elapsed, status = probe(name, command, args.timeout)
            results[name].append((elapsed, status))
            failed |= status != "PASS"
            print(f"  {name:<20} {elapsed:>7.2f}s  {status}", flush=True)
    print("\n=== Summary (seconds) ===")
    for name, items in results.items():
        values = [duration for duration, _ in items]
        count = sum(status != "PASS" for _, status in items)
        print(f"{name:<20} min={min(values):.2f} median={statistics.median(values):.2f} "
              f"max={max(values):.2f} failures={count}")
    print("RESULT: " + ("DIAGNOSTIC OUTLIERS/FAILURES OBSERVED" if failed
                        else "DIAGNOSTIC COMPLETE: NO FAILURES IN THIS SAMPLE"))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
