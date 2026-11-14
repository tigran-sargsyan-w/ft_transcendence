# Unified startup — performance benchmarks

This directory contains **operator-run development VM diagnostics**, not part of
application startup, provisioning, migrations, or CI by default.

Run these scripts **from the repository root** on the already initialized Ubuntu
26.04 development VM. The baseline is recorded in [baseline.md](baseline.md).

## Tools

- `benchmark_unified_startup.py` measures wall-clock latency of Docker CLI,
  PostgreSQL readiness, the complete health check, and warm unified startup.
- `profile_unified_calls.py` instruments Python `subprocess.run` calls using a
  short-lived `sitecustomize.py` file inside a private temporary directory.
  Reports durations, exit codes, **allowlisted executable categories**, and
  source-file/function names. It never logs arguments, environment variables,
  SQL, stdout, stderr, DSNs, Vault tokens, or Shamir shares.

## Preconditions

- Work from the root of the **same checked-out revision** on each comparison.
- Vault is already initialized and unsealed; the stack is healthy.
- No concurrent developer startup, migrations, Docker maintenance, or other
  VM-intensive workloads. Note VM hardware, resource limits, and cache state.
- Do **not** run `make initialize`, `make recover`, `docker compose down -v`,
  image pruning, migrations, or Vault resealing to obtain benchmark samples.
- `benchmark_unified_startup.py --read-only` and all profiler modes execute
  only checks. **Without `--read-only`, the benchmark executes `make up`.**
  Normally it is a no-op on a healthy VM, but if readiness changes between
  preflight and execution, the guarded orchestrator may start an existing
  trusted stopped container. Do not use that mode during concurrent work.
- The profiler temporarily injects Python instrumentation into child
  processes through `PYTHONPATH`; use it only on this **trusted** development
  checkout, not while running sensitive maintenance or unseal workflows.

## Reproduce the baseline

```sh
python3 tests/benchmarks/profile_unified_calls.py --self-test
python3 -m py_compile \
  tests/benchmarks/benchmark_unified_startup.py \
  tests/benchmarks/profile_unified_calls.py

# Checks only; repeat 3 samples per operation.
python3 tests/benchmarks/benchmark_unified_startup.py --read-only --runs 3

# Guarded routine startup plus read-only checks (healthy VM only).
python3 tests/benchmarks/benchmark_unified_startup.py --runs 3

# Read-only per-stage timings; checks have transitive dependencies.
python3 tests/benchmarks/benchmark_unified_startup.py \
  --read-only --runs 1 --stages
```

## Pinpoint slow nested calls

```sh
python3 tests/benchmarks/profile_unified_calls.py \
  --command health --runs 3 --top 15
python3 tests/benchmarks/profile_unified_calls.py \
  --command postgres-roles --runs 2 --top 15
python3 tests/benchmarks/profile_unified_calls.py \
  --command support-services --runs 2 --top 15
```

The profiler measures *wall-clock* subprocess durations. A parent process
can include time spent in several children; **nested call durations overlap**
and must not be added to calculate total runtime. A slow composite check is
not necessarily a slow named service. The profiler may not observe a child
which intentionally strips `PYTHONPATH` or executes without Python.

## Interpretation and reporting

- Compare the **same workload** on the **same VM** before/after an optimization.
- Report individual runs, median, range, failures and outliers, not just the
  fastest run. Use more repetitions when diagnosing intermittent slowdowns.
- Separate warm no-op startup, deep health checks, recovery, and cold install.
  **Cold install is not measured by these scripts**; it needs a separate clean
  and backed-up test VM.
- Preserve the current fail-closed security checks and use the existing
  readiness, reboot recovery and idempotency acceptance suites after changes.
- Only commit **sanitized aggregate measurements**. Never commit Vault init
  material, logs, command arguments, rendered secrets, private Docker inspect
  output, application rows or host-specific secret paths.
- Stop on a failed check; investigate the original read-only check manually.
  Neither benchmark attempts cleanup or repairs.
