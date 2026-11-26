# Development startup idempotency and fail-closed acceptance (Phase 6D)

This operator-run check applies to the **already initialized and healthy**
Ubuntu development VM used for `ft_transcendence` issue #115. It is not a
first-time installation, load test, destructive failure-injection suite, backup
procedure, or production disaster-recovery certification.

## Safety boundary

- Run from the checked-out project on the VM as the normal Docker-enabled user;
  **not** with `sudo`. The development Vault must already be initialized and
  unsealed, and the full stack healthy.
- Do not run concurrently with another bootstrap, migration, Docker maintenance,
  team deployment, or container restart. Stop if anything else changes resources.
- The default routine-start acceptance never invokes `--initialize`, `docker compose down -v`, `docker volume rm`,
  Vault init, AppRole rotation, or manual migration just to satisfy this test.
- This acceptance **does not** deliberately stop or restart containers, seal
  Vault, drop network connections, or manipulate PostgreSQL data.
- No unseal shares, root/admin tokens, DSNs, SecretIDs, or full Docker inspect
  records are printed, written to files, or transmitted by the test.

## Offline tests (WSL or VM)

```sh
python3 -m unittest discover -s scripts/dev -p 'test_bootstrap_unified.py' -q
python3 -m unittest discover -s scripts/dev -p 'test_verify_startup_idempotency.py' -q
python3 -m unittest discover -s scripts/dev -p 'test_acceptance_progress.py' -q
python3 -m unittest discover -s scripts/dev -p 'test_bootstrap_plan.py' -q
python3 -m py_compile scripts/dev/verify_startup_idempotency.py
```

The mock-based failure cases include unhealthy baselines, changed container
IDs or start timestamps, newly created project resources, network replacement,
changes to Prisma migration history, disallowed commands, and a failed start.
The existing unified bootstrap suite separately covers Shamir seal handling,
failed unseal, unsafe Compose identities, migration lock contention, state
races and missing containers. Unit tests do not modify Docker.

## Real VM acceptance: no destructive fault injection

First run the read-only baseline:

```sh
./scripts/bootstrap.sh --health-report
python3 scripts/dev/verify_startup_idempotency.py --check-only
```

Then, on the healthy VM, execute **three guarded no-op startups**:

```sh
python3 scripts/dev/verify_startup_idempotency.py --runs 3
```

The runner performs:

1. Trusted complete-stack health preflight; abort if unhealthy.
2. Memory-only inventory of twelve core containers, twelve named
   volumes, three core networks, the optional Vault UI HAProxy gateway
   and its edge network, and *metadata-only* Prisma migration history.
   The gateway is checked against its operator-enabled state.
3. For each attempt: `./scripts/bootstrap.sh --start`, compare snapshots,
   `--health-report`, compare again. Stop immediately if anything changes.
4. Verify container IDs, creation time, restart counts and `StartedAt`;
   volume creation times, drivers and mountpoints; network IDs; project resource
   names; and a server-side hash of migration names, checksums and statuses.

Expected output concludes with:

```text
RESULT: STARTUP IDEMPOTENCY VERIFIED
```

This means that three sequential routine `--start` calls did not restart or
recreate trusted containers, change named storage/network identities, create
new project resources, or alter Prisma migration history while the system
remained healthy. **It does not prove that the application's data rows, Vault
KV secret values, or external host files were unchanged.** Existing stage
health checks verify database roles, grants, Vault agent/credentials presence,
backend API, collector, graph engine and HTTPS. Deep credential-rotation and
failure-injection scenarios belong to their separate work items.

## On a failure

The runner exits nonzero and never attempts cleanup, rollback, migration,
credential repair or resource replacement. It prints only the failing resource
name or category. Investigate with:

```sh
./scripts/bootstrap.sh --health-report
./scripts/bootstrap.sh --plan
# Only non-secret status metadata:
docker ps -a
```

Do not use `docker compose up`, `make up`, `--initialize`, or key rotation as an
unreviewed workaround. Preserve the VM and log output for debugging. If the
health check fails due to a transient Docker CLI timeout, inspect individual
`--check-*` stages manually; do not assume success after retry.

## Acceptance record

Record commit SHA, `unittest` results, `--check-only` and `--runs 3` exit codes,
final `--health-report`, and whether the browser is accessible at
`https://transcendence.test:8443`. Do not record secret values or full Docker
inspect output. The already completed Phase 6B reboot test is separate
supporting evidence; Phase 6D does not require another reboot.

## Interrupted first initialization (Issue #160)

This is distinct from the routine-start acceptance test above. An interrupted fresh setup may have created trusted resources already. Check the active stage using the reported read-only check and make plan. After reviewing partial state, rerun make initialize to continue missing guarded operations. Existing initialized sealed Vault requires manual unseal with make recover; if application provisioning is incomplete, recovery can stop at a missing service, after which reviewed initialization may resume. Never delete persistent volumes or rotate credentials just to resume. Cancellation during Vault recovery-material persistence waits for the critical write but cannot protect against SIGKILL or power loss.


### Explicit initialize identity acceptance (Issue #160)

On an already healthy development VM, opt in separately with:

```sh
python3 scripts/dev/verify_startup_idempotency.py --initialize --runs 3
```

This invokes the actual guarded initialize path, compares resource identities,
restart counts and migration history immediately after each run and again after
health verification. It stops on drift without rollback. The default
`make acceptance` remains routine-start-only; `--check-only` is read-only and
mutually exclusive with `--initialize`. Never run the opt-in test on an unhealthy
or partially provisioned environment; use it on an isolated development VM.

## Shared V3 progress (Issue #166)

The acceptance parent uses the shared V3 progress renderer. Its stages are the
healthy baseline followed by each requested guarded attempt. Nested
observations cover inventory capture, optional gateway verification,
container identities and restart metadata, volume/network inventory and
lifecycle metadata, migration history comparison and health. Pre-health
and post-health comparisons remain distinct and in their original order.
The progress count includes the baseline, so default acceptance has four
stages for three attempts; check-only has one stage. The existing PASS
and RESULT markers remain authoritative together with the exit code.

```sh
make acceptance
FT_DEV_BOOTSTRAP_VERBOSE=1 make acceptance
python3 scripts/dev/verify_startup_idempotency.py --check-only
```

The child commands keep their existing suppressed-output subprocess
path; there is no second live renderer, new Docker inspection or new
database query for display. CI, redirected output, NO_COLOR and
FT_DEV_STAGE_TIMING keep the plain-output policy. The existing V3 resize
limitation remains.

For #166 acceptance, first run the offline suites (including simulated
missing/partial plan state and drift rejection). On the already healthy
isolated VM, run plan, baseline check-only and three guarded starts, then
health/check-all. Check interactive and verbose output, actual exit codes
and the preserved result markers. Do not remove resources to simulate a
fresh plan and do not repeat a clean installation for this presentation
change. The explicit initialize mode remains covered by offline tests;
no extra live provisioning is required for #166.

Record the tested commit, actual results and timings only after execution.
The implementation and offline test additions do not themselves establish
that the VM acceptance or CI has passed.
