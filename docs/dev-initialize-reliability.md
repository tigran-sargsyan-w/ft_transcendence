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

To request persistent reviewed substeps, retry reasons and timed child operation summaries:

```sh
FT_DEV_BOOTSTRAP_VERBOSE=1 make initialize
```

These flags can be combined. Neither exposes raw child output. Verbose mode
retains reviewed substeps, operation labels, actual child durations, exit codes
and diagnostic counts. Unknown child output is never forwarded; the reported
read-only check remains the source for detailed local diagnosis.

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

The fixed 21-stage display includes system dependencies and Docker images before the 19 existing service stages. CACHED means a fresh readiness check passed, not an on-disk cache. DONE means the guarded preparation and required verification succeeded. Optional Vault UI remains SKIPPED. FAILED, CANCELLED and ACTION REQUIRED stop the sequence; later stages are not executed. ACTION REQUIRED is used only for verified Docker group enrollment/session refresh and preserves a nonzero exit status.

An interactive terminal colors only the status field, uses adaptive-width stage columns and a completed-stage progress bar, commits one final row per stage and previews transient reviewed substeps. The renderer limits updates to five frames per second (apart from phase transitions and notices) and overwrites only changed rows. Pending stages are previewed within the available terminal height. MISSING means a reviewed absence event; NOT READY means a probe did not establish readiness. Neither authorizes provisioning or changes an exit code. UNCONFIRMED means a named child event did not report completion; no success is inferred. A terminal-stage failure still stops the sequence. CI, NO_COLOR, redirected output, verbose mode and timing mode use plain append-only text. The installer authenticates using sudo -v on the controlling terminal, then uses sudo -n for actual package/service operations so their progress stays live. Host operations that may invoke sudo indirectly (mkcert) clear the transient region and pause repainting until the foreground command returns; elapsed time still includes authentication and execution. This avoids interfering with the controlling terminal or intercepting passwords. Noninteractive initialization requires any necessary sudo authorization to be available already; it never requests or stores a password itself.

Each operation retains its existing security checks. The internal stage dispatch is allowlisted and receives the held orchestrator lock descriptor, not a global yes flag. The Linux CA workflow will trust only an already trusted CA or a newly generated, recorded and validated CA. Unexpected CA paths, files and untrusted existing CAs are refused. Windows trust is outside this automatic path. Docker enrollment is restricted to a checked local socket and still requires normal sudo; sign out/in when instructed.

Ctrl+C/SIGTERM stop further stages. Vault initialization defers cancellation while the initial recovery response is being saved durably; the parent waits and does not force-kill that critical section. An already initialized sealed Vault is not automatically unsealed. Cancellation is not a rollback: Docker/OS operations may have completed or be in flight. Review stage state with make plan and the reported check before rerunning initialize for a genuinely partial fresh install. Use make recover for an initialized sealed Vault. A power loss, SIGKILL or uncertain/partial recovery-file write requires manual recovery; never delete the recovery file or volume to retry.

Raw child output is not forwarded or saved, including verbose mode. Normal mode keeps reviewed safety notices, actionable failures and indications of unclassified warning/error output. Verbose mode adds child exit codes, line counts and reviewed operation transitions; routine successful stderr and expected cancellation do not produce process-statistics summaries in normal mode. Run the reported maintenance check locally for detailed diagnosis; do not share unreviewed logs or secret material.

## Issue #160 first-round VM findings

On Ubuntu 26.04 amd64, commit aae14ad completed 835 offline tests (6 WSL-only
skips), routine startup acceptance (3/3), read-only cancellation and Docker
pull cancellation (exit 130), and a subsequent initialization from partial
images (500.1 s). Repeated initialize on a healthy stack took 53.0 s;
redirected output contained no escape or carriage-return bytes (52.9 s).
Fresh initialization once failed at Grafana HTTPS (512.7 s), and resuming
an uninitialized existing Vault preserved its container identity but once
failed before Grafana creation. Later independent checks and manual Grafana
preparation succeeded. The specific first-round failed probes were not retained.

The follow-up implementation adds a typed bounded Prometheus readiness check
at the shell prerequisite boundary, bounded retries for explicit Grafana HTTPS
502/503/504 responses, and distinguishes validated missing Grafana credentials
from a failed inspection. Identity, authorization, WAF and TLS checks remain
required. This is not proof of the original failure causes or of a fixed
cold-VM failure rate. Repeat clean-VM runs and benchmark the final commit.

For an explicitly opted-in comparison of resource identities around repeated
initialize, on a healthy development VM only:

```sh
python3 scripts/dev/verify_startup_idempotency.py --initialize --runs 3
```

The offline cancellation integration test simulates the Vault server response
but exercises the real recovery-file writer, fsync, isolated child and signal
handling. A real Vault server cancellation within the critical section and
manual unseal/resume remain live acceptance checks. Cold-VM and full CI
results for #160 must be recorded separately from the historical #157 benchmark.


## Issue #160 second-round VM findings

On Ubuntu 26.04 amd64, branch commit a2786a6 passed fresh initialization
(569.0 s after Docker group session refresh), resume from partial Docker
images (537.4 s), and resume with an existing uninitialized Vault (439.6 s).
Each completed 21/21 stages without an approval prompt; the earlier Grafana
failures were not observed in these three runs. The resumed Vault retained
its container ID and had three shares, threshold two, and an unsealed state;
its recovery file was owned by the operator with mode 0600 and nonzero size.
Health checks passed. Healthy initialize and routine startup acceptance both
passed 3/3 without resource identity, restart-count or migration-history drift.
Read-only, image-pull and pre-initialization Vault cancellations returned 130.

The cached initialize times were 59.0 s (TTY), 58.1 s (redirected), 53.2 s
(CI), 54.5 s (NO_COLOR), and 54.6 s (verbose). The redirected log had 46
lines, zero ANSI escape bytes and zero carriage returns. These are individual
VM measurements, not a controlled estimate of renderer overhead. Visual
issues included full-region flicker, misclassified missing prerequisites,
no final result color, excessive normal-mode statistics and transient rows
occasionally overlapping committed rows. V3 addresses these in presentation
and reviewed event handling; real terminal resizing and the full VM test
matrix must still be revalidated. No live cancellation inside the Vault
persistence critical section or manual sealed-Vault recovery was exercised.

For the final performance comparison, alternate repeated healthy TTY and
redirected runs under the same VM configuration, terminal geometry and
workload; record each run and compare medians, not individual timings. Then
use the existing guarded clean-VM benchmark separately. Do not mix partial
image resume timings into cold-install medians. The renderer-only synthetic
workload below uses 21 stages, 40 refresh requests per stage at 150 ms clock
increments and four reviewed events per stage. It requires no Docker or
network access and measures writes and bytes, not end-to-end initialization.

```sh
python3 tests/benchmarks/benchmark_progress_renderer.py --baseline a2786a6
```


## Issue #160 third-round VM findings and V4 follow-up

At 28f5654, the Ubuntu VM completed 861 offline tests with six skips and
no failures (867 total). A cold post-enrollment installation completed
21/21 stages in 563.1 s without approval prompts. Both application and
Grafana HTTPS opened in the browser without untrusted-certificate warnings.
Repeated initialize and routine startup acceptance each passed 3/3 with no
resource identity, restart or migration-history drift. Read-only cancellation
returned 130 and left the full health report passing. Redirected output had
46 lines and no ANSI escape or carriage-return bytes. The synthetic renderer
benchmark (120 columns, 32 rows, seven repetitions) measured 464 writes,
240188 bytes and 336 full-row erases versus V2's 1745, 849016 and 12432.
The operator confirmed that steady-size terminal flicker disappeared.

A live terminal resize, however, reflowed old lines and broke the V3 cursor
anchor even though initialization completed successfully. V4 confines
repaint operations to the alternate screen and retains complete stage names,
actual elapsed and cumulative times, and notices for the final primary-screen
log. Only the viewport is redrawn on resize; small viewports show a bounded
selection of history and pending operations. Ctrl+C and normal exception
paths restore the primary screen. SIGKILL or a terminal crash cannot run
cleanup; if necessary, restore the terminal locally with `reset`.

In a separate reboot/recovery run, the sealed Vault guard and manual Shamir
unseal succeeded, but the already-running backend-agent initially failed
readiness and two recovery attempts stopped. A later health report passed
the agent but failed the backend. The exact cause was not captured. V4 adds
a read-only polling budget of up to 60 seconds for the verified, existing,
running backend-agent in explicit `make recover` only. Existing Docker
inspection and full-readiness check timeouts are additional. Every poll
pins the inspected ID and revalidates identity and running state; once Docker
health is healthy, the original transitive check must pass. No automatic
restart, recreation, credential provisioning, migration or unseal is added.
Ordinary `make up` retains its fail-closed policy. This is a targeted readiness
improvement, not proof that all reboot-recovery failures are fixed.

Manual Docker-pull and pre-init Vault cancellation/resume evidence is from
V2; those scenarios were not repeated in V3. Live critical-section Vault
cancellation and sealed recovery after that cancellation remain unverified.
V4 acceptance should focus on resize (including cancellation), regression
tests, normal startup/initialize identity and the targeted reboot-recovery
case if an appropriate disposable VM is available. A new cold-install run is
not required merely to verify resize. Final end-to-end timing measurements
remain separate from the renderer-only benchmark.


## Issue #160 fourth-round VM findings and V5 follow-up

At 9cee4d4, 874 offline tests ran (868 passed, six skipped, no failures).
Fresh installation on the clean Ubuntu VM paused for Docker group enrollment
(32.0 s) and completed all 21 stages after session refresh (518.3 s). Live
resize no longer duplicated or overlapped stages, and the normal terminal
history returned on completion. Two read-only cancellations (postgres-roles
at 8.8 s and backend at 19.2 s) restored the prompt, reported Error 130,
and were followed by a passing health report. The operator disliked the
alternate-screen transition, inconsistent alignment across sudo pauses and
truncated or misaligned labels at narrow widths. V5 changes presentation only:
inline display, conservative resize fallback, one shared live status column,
ellipses and a consistently formatted final report when needed. No provisioning,
resume, cancellation or recovery decisions are changed. The old preview may
remain in scrollback after a resize; the final report is authoritative.

V4 synthetic benchmark vs V3: 445 vs 464 writes, 242216 vs 240188 bytes,
80 vs 336 full-row erases. The two median renderer elapsed-time measurements
were 37.80 vs 19.38 ms and 31.20 vs 21.48 ms. This workload does not model
terminal rendering or full installation. V5 needs targeted inline/resize,
cancellation and final-report verification on an already healthy VM, not a
repeated fresh install. The targeted live reboot-recovery test and final
end-to-end benchmark remain pending.


## Issue #160 fifth-round VM findings and V6 follow-up

At 67da387, 879 offline tests ran (873 passed, six skipped, no failures).
A clean Ubuntu VM completed package enrollment with the expected action
required pause (27.9 s), then completed all 21 stages (518.1 s) without
approval prompts. Inline output before resize and the final aligned report
worked, but V5's permanent append-only fallback removed live nested events,
timers and the progress bar and produced repeated phase lines. The operator
rejected this UX. V6 replaces that fallback with a short resize settling
interval and a new inline preview, retaining the same stage and substep
formatting. An invalidated snapshot may remain visible above the new region;
no attempt is made to erase unknown reflowed terminal history. The final
report remains authoritative. CI, NO_COLOR, redirected and verbose output
retain their previous contracts. No initialization or recovery decisions
are changed. V6 acceptance is renderer-only on a healthy VM; do not repeat
cold installation to test the display.


## Issue #160 final renderer selection

At b920120, all 25 targeted V6 renderer tests passed on the Ubuntu VM.
However, live resize left a stale preview followed by a new preview; the
operator rejected this output as well. The operator explicitly selected
V3 (28f5654) as the preferred presentation and accepted its known resize
limitations rather than introducing further display modes. Restore the V3
renderer, its matching tests and the original initialize integration exactly.
The V4 bounded backend-agent recovery wait and its tests, along with the
signature-independent image boundary test, remain in place. The V4–V6
sections above are historical experiments, not the current renderer contract.

The selected renderer is inline, does not use an alternate screen, and does
not switch modes or print a duplicate final report on resize. Terminal
reflow can produce misaligned, overlapping or repeated rows when changing
window dimensions; keep the terminal size stable during initialization for
the best display. This is a known visual limitation, not a claim of resize
safety. CI, NO_COLOR, verbose and redirected output keep the V3 plain-log
contract. No fresh installation is required to recheck this restoration.
The live reboot-recovery acceptance and controlled end-to-end benchmark
remain pending. Live Vault critical-section cancellation remains deferred.
