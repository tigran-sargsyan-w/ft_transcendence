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

The historical #157 initializer retained individual confirmations. Issue #160
removes them from the fixed development initialization path. It must stop on invalid existing resources,
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

To request additional safe child diagnostic summaries:

```sh
FT_DEV_BOOTSTRAP_VERBOSE=1 make initialize
```

These flags can be combined. Neither exposes raw child output. Verbose mode
reports child exit codes and diagnostic counts; targeted read-only checks can
be run separately.

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

### Reproducible benchmark tool

The repository includes:

- `scripts/dev/benchmark_initialize.py`
- `tests/benchmarks/test_benchmark_initialize.py`

The benchmark runs the guarded `make initialize` workflow using
an interactive PTY, measures wall-clock time and stage checks,
and saves structured JSON metrics outside the repository.

It requires an exact expected Git commit SHA and a result label.
Use the historical #157 tool to reproduce historical approval-based baselines.

The runner requires a clean Git working tree and refuses detected
existing project containers, volumes, networks and local Vault
initialization material. It also requires an explicit disposable-VM
authorization before an installation benchmark.

The #160 runner expects zero installer approval prompts, refuses any that
appear, and records completed stage durations in schema version 2. It never
sends an affirmative response. Historical #157 results below remain unchanged.
The operator must still independently verify the restored snapshot.

Example on a deliberately restored disposable VM:

```sh
SHA=$(git rev-parse HEAD)

python3 scripts/dev/benchmark_initialize.py \
  --expected-commit "$SHA" \
  --preflight-only

python3 scripts/dev/benchmark_initialize.py \
  --expected-commit "$SHA" \
  --label confirmation-free \
  --run \
  --approve-disposable-vm
```

The full run also requires the operator to type `DISPOSABLE VM`.

Never use this benchmark to repair or reinitialize an existing
Vault installation. It is not intended for production.

### Test environment and methodology

- Virtualization: VirtualBox.
- Guest: Ubuntu 26.04 LTS, amd64.
- VM resources: 4 vCPUs and approximately 3.3 GiB RAM.
- Initial snapshot: `PRE-INIT-DEPS-READY`.
- Baseline Git commit:
  `dca1e38dc0e78a3e67b42b588a9922b325b076b6`.
- Optimized Git commit:
  `9e8222e9e4e90761a87b6a4f26998ce8cbc326d6`.

Each run began from the same restored snapshot, with the
corresponding Git commit checked out and a clean working tree.

All five historical measurements used the original benchmark
helper, with its expected SHA adjusted for the optimized runs.
The repository-integrated benchmark tool was added afterward
and was not used to produce these historical measurements.

The measured wall-clock interval covers the complete first-run
initialization, including prerequisite checks, Docker image pulls,
host TLS configuration, Vault, PostgreSQL, Prisma migrations,
application services, Prometheus and Grafana.

Each run used the reviewed set of 20 operator confirmations.
No manual second initialization attempt was needed for the
five successful measured runs.

### Individual results

| Run | Version | Total seconds | Exit | Outer checks | Confirmations |
| --- | --- | ---: | ---: | ---: | ---: |
| B1 | Baseline | 646.890 | 0 | 69 | 20 |
| B2 | Baseline | 668.871 | 0 | 69 | 20 |
| O1 | Optimized | 506.431 | 0 | 49 | 20 |
| O2 | Optimized | 510.478 | 0 | 49 | 20 |
| O3 | Optimized | 499.376 | 0 | 49 | 20 |

All five runs finished with:

```text
RESULT: UNIFIED INITIALIZATION READY
```

The optimized run metrics files were:

- `baseline-20261123T152602Z.json`
- `baseline-20261123T154809Z.json`
- `baseline-20261123T160506Z.json`

These files were produced by the original helper, which retained
the historical `baseline-*` naming even for optimized runs.
The commit SHA inside each JSON identifies the actual version.

The metrics contain no raw command output and are stored
outside Git.

### Aggregate comparison

| Metric | Baseline | Optimized |
| --- | ---: | ---: |
| Successful clean installs | 2/2 | 3/3 |
| Mean duration | 657.881 s | 505.428 s |
| Median duration | 657.881 s | 506.431 s |
| Fastest run | 646.890 s | 499.376 s |
| Slowest run | 668.871 s | 510.478 s |
| Time range | 21.981 s | 11.102 s |
| Outer-stage checks | 69 | 49 |
| Operator confirmations | 20 | 20 |

Observed mean improvement:

- **152.452 seconds saved**, approximately 2 minutes 32 seconds.
- **23.17% reduction** in initialization duration.
- Approximately **1.30x faster**.
- **20 fewer outer checks**, a 28.99% reduction.

### Verification and acceptance

After each of the three optimized clean installations:

- `make health`: PASS.
- `make check-all`: PASS.
- `make vault-ui-status`: expected disabled state.
- `make acceptance`: PASS, three guarded startup attempts.
- Git working tree: clean.

Nine guarded startup attempts were verified in total.
The acceptance runner found no detected resource-identity,
container-restart or Prisma migration-history drift.

Offline regression results after the bootstrap improvements:

```text
Ran 131 tests
OK (skipped=6)
```

After introducing the reusable benchmark tool and its
12 offline regression tests:

```text
Ran 143 tests
OK (skipped=6)
```

The new benchmark tool has passed offline tests but has
not yet been exercised in a complete clean-VM installation.

### Limitations

The original intermittent Prometheus probe failure was not
conclusively reproduced. Three successful optimized first-run
installations provide engineering evidence, not a statistical
guarantee that rare failures cannot recur.

The benchmark sample is small: two baseline runs and three
optimized runs. Image download speed, VM scheduling, caching
and external conditions can affect the result.

The 69-to-49 comparison covers outer orchestrator checks.
It does not count all nested Docker or transitive service probes.

Stage preparation durations reported by the original helper
are approximate, inferred from neighboring timed checks.

The unchanged confirmation count documents that operator
approvals were retained. Separate checks and regression tests
are required to assess the preservation of security guarantees.

Vault UI HAProxy remained intentionally disabled during the
clean-install runs. The enabled gateway lifecycle was covered
by offline tests, not by live enabled-gateway acceptance here.

Some preparation stages still print repeated dependency
messages. Output cleanup remains separate work and must not
suppress validation, confirmations or failure diagnostics.

Do not store raw terminal recordings, Vault recovery material,
SecretIDs, root tokens, DSNs or other credentials in Git or
benchmark artifacts.

## Scope and limitations

This is development infrastructure, not a production-grade automated
recovery, HA, backup, or security certification.

Related guides:

- [Developer onboarding](dev-getting-started.md)
- [Health reporting](dev-health-report.md)
- [Startup idempotency](dev-startup-idempotency.md)
- [Vault UI administration](dev-vault-ui-on-demand.md)

## Issue #160 initialization progress and cancellation

The fixed 21-stage display includes system dependencies and Docker images before the 19 existing service stages. CACHED means a fresh readiness check passed, not an on-disk cache. DONE means the guarded preparation and required verification succeeded. Optional Vault UI remains SKIPPED. FAILED and CANCELLED stop the sequence; later stages are not executed.

An interactive terminal repaints the active row. CI, redirected output, verbose mode and timing mode use plain append-only text. Host operations may temporarily pause repainting to let normal sudo authentication use the controlling terminal. Noninteractive initialization requires any necessary sudo authorization to be available already; it never requests or stores a password itself.

Each operation retains its existing security checks. The internal stage dispatch is allowlisted and receives the held orchestrator lock descriptor, not a global yes flag. The Linux CA workflow will trust only an already trusted CA or a newly generated, recorded and validated CA. Unexpected CA paths, files and untrusted existing CAs are refused. Windows trust is outside this automatic path. Docker enrollment is restricted to a checked local socket and still requires normal sudo; sign out/in when instructed.

Ctrl+C/SIGTERM stop further stages. Vault initialization defers cancellation while the initial recovery response is being saved durably; the parent waits and does not force-kill that critical section. An already initialized sealed Vault is not automatically unsealed. Cancellation is not a rollback: Docker/OS operations may have completed or be in flight. Review stage state with make plan and the reported check before rerunning initialize for a genuinely partial fresh install. Use make recover for an initialized sealed Vault. A power loss, SIGKILL or uncertain/partial recovery-file write requires manual recovery; never delete the recovery file or volume to retry.

Raw child output is not forwarded or saved, including verbose mode. Reviewed fixed notices, warning/error counts, actual exit codes and a targeted check are provided instead. Run the reported maintenance check locally for detailed diagnosis; do not share unreviewed logs or secret material.

Issue #160 cold-VM and full CI results must be recorded separately from the historical #157 benchmark. No measurements for #160 are claimed here.
