# Performance baseline — `feat/vault-unified-startup`

**Measured:** 2026-11-14, already initialized Ubuntu 26.04 (amd64) development VM;
Vault unsealed; warm stack. **Source:** operator-provided benchmark output.
This is the baseline **before** performance optimizations; no cold-install
measurements were performed.

**Environment context:** approximately 5.2 GiB RAM and 3.8 GiB swap were
reported for this VM during the earlier reboot recovery check; those figures
were **not remeasured during this benchmark**. The exact CPU, storage type,
Docker image cache state, and other concurrent VM workloads were not recorded.

## Warm-stack measurements (wall-clock seconds)

| Operation | Read-only run A (3 samples) | Warm run B (3 samples) | Stage run C (1 sample) | Total observed range | Median of available samples |
| --- | --- | --- | --- | --- | --- |
| `docker ps` | 0.22, 0.17, 0.23 | 0.17, 0.17, 0.22 | 0.17 | 0.17–0.23 | 0.17 |
| `--check-postgres` | 1.15, 0.93, 1.09 | 1.00, 0.90, 1.08 | 0.92 | 0.90–1.15 | 1.00 |
| `make health` | 28.72, 105.58, 32.58 | 36.01, 10.90, 12.90 | 117.13 | 10.90–117.13 | 32.58 |
| `make check-all` | 11.32, 11.72, 12.44 | 13.08, 10.91, 12.92 | 10.85 | 10.85–13.08 | 11.72 |
| `make up` (warm) | Not run | 56.80, 12.91, 40.52 | Not run | 12.91–56.80 | 40.52 |

Healthy preflight wall times: A 28.68 s; B 11.21 s; C 11.56 s.

## Per-stage read-only diagnostics (run C only)

| Stage | Elapsed (s) | Stage | Elapsed (s) |
| --- | ---: | --- | ---: |
| host | 0.79 | resources | 1.33 |
| vault | 0.89 | vault-config | 2.72 |
| vault-secrets | 3.77 | postgres | 1.08 |
| postgres-roles | 45.71 | migrations | 4.98 |
| backend-agent | 6.31 | backend | 7.16 |
| support-services | 59.45 | frontend | 9.48 |
| nginx | 9.89 | | |

## Initial interpretation (hypotheses, **not** proven root causes)

- `docker ps` is consistently fast; standalone PostgreSQL check is around 1 s.
- `make check-all` is relatively stable (~11–13 s), while `make health` varies
  from 11 s to nearly 2 minutes. The latter performs extra dependency
  localization if its initial end-to-end probe fails. An intermittent failure
  and subsequent repeated checks could explain some long runs.
- `make up` is also unstable for an already healthy system (13–57 s).
- The single slow `postgres-roles` and `support-services` stage results cannot
  establish their own intrinsic cost: each stage includes repeated transitive
  dependency checks, and the underlying cause may be another Docker/psql call.
- Next: profile the **internal subprocess calls**, identify which calls have
  long tails or timeouts, optimize without relaxing trust/identity/ACL/migration
  checks, and rerun comparable measurements.

## Future comparison (fill after a reviewed optimization)

| Metric | Baseline median (s) | Optimized median (s) | Notes |
| --- | ---: | ---: | --- |
| `make up` (warm) | 40.52 | TBD | 3 baseline samples; high variance |
| `make health` | 32.58 | TBD | 7 baseline samples; high variance |
| `make check-all` | 11.72 | TBD | 7 baseline samples |
| Standalone PostgreSQL check | 1.00 | TBD | 7 baseline samples |

**Acceptance gate:** no regressions in offline unit tests, `make health`,
`make check-all`, full reboot recovery (when a change affects it), and the
three-run idempotency acceptance. Do not treat improved speed as a substitute
for these correctness and security requirements.
