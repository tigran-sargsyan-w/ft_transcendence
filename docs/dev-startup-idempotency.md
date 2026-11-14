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
- Never invoke `--initialize`, `docker compose down -v`, `docker volume rm`,
  Vault init, AppRole rotation, or manual migration just to satisfy this test.
- This acceptance **does not** deliberately stop or restart containers, seal
  Vault, drop network connections, or manipulate PostgreSQL data.
- No unseal shares, root/admin tokens, DSNs, SecretIDs, or full Docker inspect
  records are printed, written to files, or transmitted by the test.

## Offline tests (WSL or VM)

```sh
python3 -m unittest discover -s scripts/dev -p 'test_bootstrap_unified.py' -q
python3 -m unittest discover -s scripts/dev -p 'test_verify_startup_idempotency.py' -q
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
2. Memory-only inventory of nine main containers, eight named persistent
   volumes, three networks, and the *metadata-only* Prisma migration history.
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
