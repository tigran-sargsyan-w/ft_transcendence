# First-run bootstrap reliability and performance (Issue #157)

This document describes development bootstrap improvements for a
fresh Ubuntu 26.04 (amd64) VM.

These changes do not introduce automatic recovery, credential rotation,
Vault reinitialization, or destructive Docker cleanup.

## Normal first-time initialization

On a genuinely fresh, supported VM, from an interactive terminal:

```sh
make initialize
```

Run as the regular developer user, without sudo.

The initializer retains individual operator confirmations for
provisioning steps. It must stop on invalid existing resources,
container identities, TLS configuration, Vault state, database
permissions, or failed service readiness.

Do not run initialize merely to repair an unhealthy existing stack.

## Check ownership and performance

The top-level bootstrap performs the trusted platform, package and
Docker preflight.

The unified orchestrator also performs one trusted preflight before
dispatching its internal checks. Allowlisted read-only checks then
invoke their existing Python stage modules directly, avoiding repeated
shell/package preflight overhead.

Stage preparation still runs through bootstrap.sh, including its
host, package, resource and dependency checks.

Read-only results are not cached across provisioning mutations.
Each stage is revalidated after preparation.

On first-run initialization, an absent stage can avoid unnecessary
repeated failed probes only when its expected Docker containers are
definitely missing. Ambiguous or partial container inventories retain
the full retry behavior.

These optimizations do not relax trusted Docker identity checks,
network or mount validation, Vault security, database checks or
service readiness requirements.

## Prometheus and Grafana readiness

Prometheus checks distinguish:

- Container absence, invalid identity, failed prerequisites and
  non-running state: non-readiness failures, not retried by Grafana.
- HTTP/metrics probe failures: a separate readiness error, retried
  by Grafana at most three times with a two-second delay between
  attempts.

Persistent probe failures remain fatal.

The HTTP probe checks Prometheus readiness, Collector target health,
and the expected Demo Lab metric. A probe failure may be transient or
persistent; bounded retries do not prove that it was transient.

The precise cause of the original first-run monitoring failure has
not yet been reproduced conclusively.

## Output and diagnostics

Normal output favors stage-level results and suppresses repeated
successful package and Docker resource inventory messages.

To include stage check timings:

```sh
FT_DEV_STAGE_TIMING=1 make initialize
```

To request additional package-preflight and resource inventory details:

```sh
FT_DEV_BOOTSTRAP_VERBOSE=1 make initialize
```

These flags can be combined. The verbose flag does not expose all
output from internal read-only probes, which remain quiet during
normal orchestration.

For a focused failure investigation, run the reported read-only
check explicitly, for example:

```sh
./scripts/bootstrap.sh --check-prometheus
./scripts/bootstrap.sh --check-grafana
make health
```

Do not share Vault tokens, Shamir shares, SecretIDs, DSNs, private
initialization files, or raw Docker inspect output.

## Offline and existing-stack verification

Run offline regressions:

```sh
python3 -m unittest discover -s tests/dev -p 'test_*.py' -q
bash -n scripts/bootstrap.sh
git diff --check
```

On an already healthy development VM:

```sh
make health
python3 scripts/dev/verify_startup_idempotency.py --check-only
make acceptance
```

The acceptance runner checks trusted application/Vault resources,
Prisma migration metadata, and the optional Vault UI HAProxy gateway
container, edge network, and operator-enabled state.

It permits an intentionally disabled gateway, but rejects unexpected
gateway restarts or lifecycle changes.

Acceptance is operator-initiated; --start can start trusted stopped
containers if the prerequisite health gate unexpectedly permits it.
Do not run concurrently with deployment or maintenance.

## Performance evidence — 2026-11-23

### Benchmark environment and methodology

The benchmark compares the original and optimized first-run
initialization using a disposable VirtualBox VM:

- OS: Ubuntu 26.04 LTS (amd64).
- Hardware: 4 vCPUs, approximately 3.3 GiB guest RAM.
- Initial state: `PRE-INIT-DEPS-READY` VirtualBox snapshot.
- Baseline commit:
  `dca1e38dc0e78a3e67b42b588a9922b325b076b6`.
- Optimized commit:
  `9e8222e9e4e90761a87b6a4f26998ce8cbc326d6`.

The same initial VM snapshot was restored independently before
each measured installation.

The corresponding Git commit was checked out before executing
the benchmark. The repository working tree was clean.

Both versions used the same benchmark procedure:

1. Restore the original VM snapshot.
2. Check out and verify the intended Git commit.
3. Run the first-install benchmark once, without manually
   restarting initialization after a failure.
4. Automatically approve only the explicitly allowlisted
   operator confirmation prompts.
5. Record total wall-clock time, exit status, stage check
   timings and confirmation count.
6. Validate the initialized stack using the trusted
   read-only checks and startup-idempotency acceptance.

The benchmark invokes:

```sh
./scripts/bootstrap.sh --initialize
```

This is the implementation used by `make initialize`.

The original helper was `~/ft_initialize_bench.py`.
The optimized runs used a copy with the expected Git SHA
updated to the optimized commit.

The benchmark includes package and Docker image preparation,
TLS setup, Vault initialization, PostgreSQL provisioning,
migrations, application startup and monitoring setup.

All measurements were taken on the same VM configuration.

### Individual results

| Run | Version | Total | Exit | Outer checks | Confirmations |
| --- | --- | ---: | ---: | ---: | ---: |
| B1 | Baseline | 646.890 s | 0 | 69 | 20 |
| B2 | Baseline | 668.871 s | 0 | 69 | 20 |
| O1 | Optimized | 506.431 s | 0 | 49 | 20 |
| O2 | Optimized | 510.478 s | 0 | 49 | 20 |
| O3 | Optimized | 499.376 s | 0 | 49 | 20 |

Every recorded installation completed with:

```text
RESULT: UNIFIED INITIALIZATION READY
```

Each optimized run completed successfully in one invocation.

The optimized benchmark's copied script retains historical
labels such as `FIRST-RUN BASELINE`, `baseline_commit` and
`baseline-*.json`. These labels do not indicate that baseline
code was executed: the recorded commit SHA identifies the
optimized implementation.

The three optimized safe-metrics JSON files were:

```text
baseline-20261123T152602Z.json
baseline-20261123T154809Z.json
baseline-20261123T160506Z.json
```

These JSON records contain non-secret benchmark metrics.
They are external benchmark evidence, not repository fixtures.

### Aggregate comparison

| Metric | Baseline | Optimized |
| --- | ---: | ---: |
| Successful installations | 2/2 | 3/3 |
| Mean initialization time | 657.881 s | 505.428 s |
| Median initialization time | 657.881 s | 506.431 s |
| Fastest initialization | 646.890 s | 499.376 s |
| Slowest initialization | 668.871 s | 510.478 s |
| Observed time range | 21.981 s | 11.102 s |
| Outer-stage checks | 69 | 49 |
| Operator confirmations | 20 | 20 |

Observed mean improvement:

- **152.452 seconds saved** per initialization.
- **23.17% reduction** in initialization time.
- Approximately **1.30x faster** initialization.
- **20 fewer outer-stage checks**, a 28.99% reduction.

The unchanged confirmation count demonstrates that the
optimization did not eliminate operator approval prompts.

### Post-installation acceptance

After each of the three optimized installations, the same
VM successfully passed:

| Verification | Result |
| --- | --- |
| `make initialize` | PASS, first invocation |
| `make health` | PASS |
| `make check-all` | PASS |
| `make vault-ui-status` | PASS, intentionally disabled |
| `make acceptance` | PASS, 3 guarded starts |
| Git working tree | Clean |

The acceptance runner found no container recreation,
container restart or drift in the tracked Docker resources
and Prisma migration-history metadata.

Across three optimized installations, this represents nine
successful guarded startup-idempotency attempts.

Offline regression verification also completed:

```text
Ran 131 tests
OK (skipped=6)
```

This corresponds to 125 passing tests and six skips.

Tests cover first-run stage behavior, dispatch, readiness
failure classification, bounded Grafana/Prometheus retries,
resource inventory, and optional Vault UI gateway acceptance.

### Limitations and remaining work

**Reliability**

Three independent successful clean installations provide
useful engineering acceptance evidence. They do not establish
a statistical guarantee of zero future first-run failures.

The original intermittent nested Prometheus HTTP-probe
failure was not conclusively reproduced. Bounded readiness
retries and regression tests address the observed failure
class, while permanent trust and prerequisite errors remain
fail-closed.

**Timing**

The benchmark records wall-clock initialization duration.
Docker image downloading, container builds, VM scheduling,
filesystem caching and network conditions can influence it.

The baseline has two measurements and the optimized version
has three. The observed improvement is reproducible in
these tests, but is not a universal performance guarantee.

Preparation durations reported by the helper are estimates
derived from neighboring timed checks.

The check counts represent outer orchestrator checks.
They are not counts of every nested Docker command or
transitive service probe.

**Security and acceptance scope**

All normal operator confirmations were preserved.
The benchmark did not deliberately bypass Vault, TLS,
Docker identity, network isolation or database validation.

Vault UI HAProxy was intentionally disabled during the
clean-install acceptance. Its enabled lifecycle is covered
by offline regression tests, not by a separate live
enabled-gateway acceptance in this benchmark.

Startup-idempotency verification tracks resource identity
and migration history; it does not prove that arbitrary
database rows, Vault secret values or host files are
unchanged.

**Output quality**

Although repeated successful package preflight messages
and resource inventory output were reduced, some
`--prepare-*` stages still print repeated transitive
dependency results.

Further output cleanup must preserve real validation,
operator prompts and useful error diagnostics.

### Conclusion

The optimized implementation completed three independent
clean installations successfully on the same disposable
Ubuntu VM snapshot.

Its mean initialization time decreased from 657.881 seconds
to 505.428 seconds, an observed improvement of 23.17%.

The three post-installation acceptance runs all verified
healthy services and startup idempotency.

The benchmark and reliability evidence support the
first-run performance improvements in Issue #157.
Remaining diagnostic and output-quality limitations are
documented above.

## Scope and limitations

This is development infrastructure, not a production-grade automated
recovery, HA, backup, or security certification.

Related guides:

- [Developer onboarding](dev-getting-started.md)
- [Health reporting](dev-health-report.md)
- [Startup idempotency](dev-startup-idempotency.md)
- [Vault UI administration](dev-vault-ui-on-demand.md)
