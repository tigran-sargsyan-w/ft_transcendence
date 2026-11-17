#!/usr/bin/env python3
"""Standalone, non-destructive latency benchmark for ft_transcendence's dev VM.

Run from repository root. Default measures warm make up (only on healthy stack),
read-only health/checks, and Docker CLI latency. Does not edit the repository,
write snapshots, provision, unseal, run migrations, or restart containers itself.
A guarded make up may start a previously stopped trusted container if the stack
becomes unhealthy between the preflight and the timed call.
"""

import argparse
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path

STAGES = (
    "host", "resources", "vault", "vault-config", "vault-secrets",
    "postgres", "postgres-roles", "migrations", "backend-agent",
    "backend", "support-services", "frontend", "nginx",
)


class BenchmarkError(Exception):
    pass


def timed_call(command, timeout):
    before = time.perf_counter()
    try:
        result = subprocess.run(command, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, check=False,
                                timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        elapsed = time.perf_counter() - before
        raise BenchmarkError(
            f"{' '.join(command)} unavailable or timed out after {elapsed:.2f}s"
        ) from exc
    elapsed = time.perf_counter() - before
    if result.returncode != 0:
        raise BenchmarkError(
            f"{' '.join(command)} failed (exit {result.returncode}, {elapsed:.2f}s). "
            "Run the command manually to inspect the failure."
        )
    return elapsed


def print_results(results):
    print("\n=== Results (wall-clock seconds) ===", flush=True)
    print(f"{'Operation':<26} {'Runs':>4} {'Min':>9} {'Median':>9} {'Max':>9}")
    for label, values in results.items():
        print(f"{label:<26} {len(values):>4} {min(values):>9.2f} "
              f"{statistics.median(values):>9.2f} {max(values):>9.2f}")
    print("\nNotes: each stage check includes its transitive dependencies, so do not "
          "add stage durations together. These are warm timings, not fresh-install times.")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=3,
                        help="Repeated samples per operation (default: 3)")
    parser.add_argument("--timeout", type=int, default=180,
                        help="Timeout for each operation, seconds (default: 180)")
    parser.add_argument("--stages", action="store_true",
                        help="Time each read-only --check-* stage once (expensive)")
    parser.add_argument("--read-only", action="store_true",
                        help="Do not invoke make up; measure only checks")
    args = parser.parse_args(argv)
    if not 1 <= args.runs <= 10 or not 5 <= args.timeout <= 1800:
        parser.error("--runs must be 1..10 and --timeout 5..1800")
    if not Path("./scripts/bootstrap.sh").is_file() or not Path("Makefile").is_file():
        parser.error("Run from the ft_transcendence repository root")
    if not shutil.which("make") or not shutil.which("docker"):
        parser.error("make and docker must be installed")

    items = [
        ("docker ps", ["docker", "ps", "--quiet"]),
        ("check-postgres", ["./scripts/bootstrap.sh", "--check-postgres"]),
        ("make health", ["make", "health"]),
        ("make check-all", ["make", "check-all"]),
    ]
    if not args.read_only:
        items.append(("make up (warm)", ["make", "up"]))

    print("=== ft_transcendence startup benchmark ===", flush=True)
    print("First verifying complete-stack health (output suppressed)...", flush=True)
    try:
        baseline = timed_call(["make", "health"], args.timeout)
        print(f"PASS: healthy baseline ({baseline:.2f}s)", flush=True)
        results = {label: [] for label, _ in items}
        for number in range(1, args.runs + 1):
            print(f"\nSample {number}/{args.runs}", flush=True)
            for label, command in items:
                elapsed = timed_call(command, args.timeout)
                results[label].append(elapsed)
                print(f"  {label:<26} {elapsed:>7.2f}s", flush=True)
        if args.stages:
            print("\nRead-only stage diagnostics (one sample each; may take time):", flush=True)
            for name in STAGES:
                label = "stage " + name
                elapsed = timed_call(
                    ["./scripts/bootstrap.sh", "--check-" + name], args.timeout
                )
                results[label] = [elapsed]
                print(f"  {label:<26} {elapsed:>7.2f}s", flush=True)
        print_results(results)
    except BenchmarkError as exc:
        print(f"\nSTOP: {exc}", file=sys.stderr)
        print("No cleanup or recovery was attempted. Check make health and "
              "the failed command manually.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
