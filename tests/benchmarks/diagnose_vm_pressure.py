#!/usr/bin/env python3
"""Correlate read-only unified health latency with guest Linux resource pressure.

No Docker metadata, subprocess output, secrets, or host-specific paths are logged.
Run on an already healthy Linux VM. The tool itself makes no changes;
underlying health checks may launch transient, self-removing Docker probes.
"""

import argparse
import os
from pathlib import Path
import signal
import statistics
import subprocess
import time

COMMANDS = {
    "health": ("make", "--no-print-directory", "health"),
    "check-all": ("make", "--no-print-directory", "check-all"),
}
PRESSURE = ("cpu", "io", "memory")


def parse_pressure(value):
    """Parse only kernel monotonic `total` microseconds; never use raw paths."""
    output = {}
    for line in value.splitlines():
        pieces = line.split()
        if not pieces or pieces[0] not in ("some", "full"):
            continue
        for field in pieces[1:]:
            if field.startswith("total="):
                try:
                    output[pieces[0]] = int(field.partition("=")[2])
                except ValueError:
                    pass
    return output


def parse_meminfo(value):
    wanted = ("MemTotal", "MemAvailable", "SwapTotal", "SwapFree")
    values = {}
    for line in value.splitlines():
        key, sep, val = line.partition(":")
        if sep and key in wanted:
            try:
                values[key] = int(val.strip().split()[0]) * 1024
            except (ValueError, IndexError):
                pass
    return values


def parse_cpu(value):
    """Return total and steal/iowait jiffies from aggregate /proc/stat CPU."""
    for line in value.splitlines():
        parts = line.split()
        if parts and parts[0] == "cpu" and len(parts) >= 9:
            try:
                values = [int(item) for item in parts[1:]]
            except ValueError:
                return None
            return {"total": sum(values), "iowait": values[4], "steal": values[7]}
    return None


def read_snapshot():
    out = {"pressure": {}, "mem": {}, "cpu": None}
    for kind in PRESSURE:
        try:
            out["pressure"][kind] = parse_pressure(
                Path(f"/proc/pressure/{kind}").read_text(encoding="ascii")
            )
        except OSError:
            pass
    try:
        out["mem"] = parse_meminfo(Path("/proc/meminfo").read_text(encoding="ascii"))
        out["cpu"] = parse_cpu(Path("/proc/stat").read_text(encoding="ascii"))
    except OSError:
        pass
    return out


def pressure_fraction(start, end, kind, item, elapsed):
    left = start["pressure"].get(kind, {}).get(item)
    right = end["pressure"].get(kind, {}).get(item)
    if left is None or right is None or elapsed <= 0 or right < left:
        return None
    return 100 * (right - left) / (1_000_000 * elapsed)


def cpu_fraction(start, end, item):
    left, right = start.get("cpu"), end.get("cpu")
    if left is None or right is None:
        return None
    total = right["total"] - left["total"]
    delta = right[item] - left[item]
    return 100 * delta / total if total > 0 and delta >= 0 else None


def format_pct(value):
    return "n/a" if value is None else f"{value:.2f}%"


def format_mib(value):
    return "n/a" if value is None else f"{value / 1048576:.0f} MiB"


def stop_group(proc):
    if proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait()


def measure(name, interval, timeout):
    before = read_snapshot()
    start = time.monotonic()
    proc = subprocess.Popen(
        COMMANDS[name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    snapshots = [before]
    timed_out = False
    try:
        while proc.poll() is None:
            snapshots.append(read_snapshot())
            if time.monotonic() - start >= timeout:
                timed_out = True
                stop_group(proc)
                break
            time.sleep(interval)
        proc.wait()
    finally:
        stop_group(proc)
    snapshots.append(read_snapshot())
    elapsed = time.monotonic() - start
    after = snapshots[-1]
    mem_available = [s["mem"].get("MemAvailable") for s in snapshots]
    mem_available = [v for v in mem_available if v is not None]
    swap_used = [
        s["mem"]["SwapTotal"] - s["mem"]["SwapFree"]
        for s in snapshots
        if "SwapTotal" in s["mem"] and "SwapFree" in s["mem"]
    ]
    print(
        f"  Wall={elapsed:.2f}s  status={'TIMEOUT' if timed_out else 'PASS' if proc.returncode == 0 else 'EXIT_'+str(proc.returncode)}",
        flush=True,
    )
    print(
        f"  Guest CPU steal={format_pct(cpu_fraction(before, after, 'steal'))}"
        f"  iowait={format_pct(cpu_fraction(before, after, 'iowait'))}", flush=True,
    )
    print(
        f"  PSI CPU some={format_pct(pressure_fraction(before, after, 'cpu', 'some', elapsed))}"
        f"  IO some={format_pct(pressure_fraction(before, after, 'io', 'some', elapsed))}"
        f"  IO full={format_pct(pressure_fraction(before, after, 'io', 'full', elapsed))}",
        flush=True,
    )
    print(
        f"  PSI memory some={format_pct(pressure_fraction(before, after, 'memory', 'some', elapsed))}"
        f"  full={format_pct(pressure_fraction(before, after, 'memory', 'full', elapsed))}",
        flush=True,
    )
    print(
        f"  MemAvailable minimum={format_mib(min(mem_available) if mem_available else None)}"
        f"  Swap used maximum={format_mib(max(swap_used) if swap_used else None)}",
        flush=True,
    )
    return elapsed, not timed_out and proc.returncode == 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--command", choices=tuple(COMMANDS), default="health")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--interval", type=float, default=0.5)
    parser.add_argument("--timeout", type=float, default=240)
    args = parser.parse_args(argv)
    if not (1 <= args.runs <= 5 and 0.2 <= args.interval <= 2 and 30 <= args.timeout <= 600):
        parser.error("runs 1..5; interval 0.2..2; timeout 30..600")
    if not (Path("scripts/bootstrap.sh").is_file() and Path("Makefile").is_file()):
        parser.error("Run from the repository root")
    if not Path("/proc/meminfo").is_file():
        parser.error("Linux /proc unavailable")
    print("=== Read-only VM pressure/latency correlation ===", flush=True)
    print("Only aggregate kernel counters and command exit status are displayed.", flush=True)
    durations = []
    for index in range(args.runs):
        print(f"\nRun {index + 1}/{args.runs}: {args.command}", flush=True)
        elapsed, ok = measure(args.command, args.interval, args.timeout)
        durations.append(elapsed)
        if not ok:
            print("RESULT: CHECK FAILED (no automatic repair)")
            return 1
    print(
        f"\nWall min={min(durations):.2f}s median={statistics.median(durations):.2f}s"
        f" max={max(durations):.2f}s",
        flush=True,
    )
    print("RESULT: READ-ONLY DIAGNOSTIC COMPLETE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
