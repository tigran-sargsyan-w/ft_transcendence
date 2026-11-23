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

## Performance evidence

The pre-change baseline was measured twice using the same disposable
VirtualBox Ubuntu VM snapshot (PRE-INIT-DEPS-READY), the same commit
dca1e38dc0e78a3e67b42b588a9922b325b076b6, and the same
interactive provisioning procedure:

| Baseline run | Total time | Result |
| --- | ---: | --- |
| 1 | 646.890 s | Success |
| 2 | 668.871 s | Success |
| Mean | 657.881 s | Baseline only |

The earlier intermittent failure during Grafana provisioning remains
a separate observation; the two successful baseline runs do not
establish that the problem cannot recur.

The optimized implementation has passed local unit tests, health
verification and three repeated startup acceptance runs.

A final before/after speedup and single-run clean-install reliability
claim require an optimized run from the same disposable VM snapshot.

Do not erase or reinitialize an existing Vault installation to run
this benchmark. Record timings, exit status, and non-secret stage
results only.

## Scope and limitations

This is development infrastructure, not a production-grade automated
recovery, HA, backup, or security certification.

Related guides:

- [Developer onboarding](dev-getting-started.md)
- [Health reporting](dev-health-report.md)
- [Startup idempotency](dev-startup-idempotency.md)
- [Vault UI administration](dev-vault-ui-on-demand.md)
