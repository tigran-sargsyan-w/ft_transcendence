# Issue #106 — Vault / Agent / PostgreSQL failure matrix

**Scope:** local development reliability, not production HA or auto-unseal. All fault injection must run in a newly provisioned, isolated `ft-cr-106-*` Clean-Room using unique Compose projects, Docker volumes, and TLS files. **Never inject failures into the ordinary development projects, the prior `ft-cr-105-*` environments, or their Raft/PostgreSQL data.**

## Baseline / existing evidence

- Local diagnostic on 2026-11-09: Vault server exited, Runtime Agent unhealthy, PostgreSQL and Backend exited; TLS certificate locally verifies and expires 2027-02-01. Readiness correctly reports `NOT READY`. This is a *non-injected* observation, not a successful recovery test.
- Offline `check-readiness.py` tests check that missing, sealed, ambiguous and unreachable dependencies fail closed. Live fault injection and restoration remain **not tested** until explicitly performed in a separate environment.
- `--mode vault` means **server/TLS prestart** only. It does **not** prove AppRole SecretID validity or migration readiness. `--mode stack` checks Vault, Runtime Agent, PostgreSQL, and Backend Docker health only.

| ID | Failure / condition | Expected evidence | Recovery criterion | Test type | Status |
|---|---|---|---|---|---|
| F01 | Vault server stopped or Docker unavailable | `NOT READY`; no automatic start, unseal, or init | After controlled restart and manual unseal in test environment, readiness returns healthy | Offline + Clean-Room | Offline PASS; live pending |
| F02 | Vault running but sealed | `Vault API: sealed`; no secret extraction | Test operator manually unseals; Agent regains health | Offline + Clean-Room | Offline PASS; live pending |
| F03 | Vault API timeout or unexpected response | `NOT READY`; no assumption that Vault is usable | Correct response and readiness after restoration | Offline + Clean-Room | Offline PASS; live pending |
| F04 | Runtime Agent unhealthy, missing rendered secret, or invalid SecretID | `NOT READY`; no fallback to hardcoded DB URL | Agent reauthenticates with valid role credentials and renders only its allowed KV secret | Offline + Clean-Room | Offline partial; live pending |
| F05 | PostgreSQL stopped/unavailable | `NOT READY`; Backend health reflects failure | PostgreSQL restarts; existing data preserved; Backend health returns | Offline + Clean-Room | Offline PASS; live pending |
| F06 | Backend stopped, starting or unhealthy | `NOT READY`; no false healthy state | Backend health endpoint succeeds after dependencies recover | Offline + Clean-Room | Offline PASS; live pending |
| F07 | Prisma migration fails or is interrupted | Startup fails closed; no stale migration secret; cleanup attempted | Retry succeeds after failure removed, restricted grants retained | Clean-Room | Pending |
| F08 | Concurrent `start-backend.sh`/`run-migrations.sh` | Host `flock` blocks the second operation | First run finishes and lock becomes available | Clean-Room or safe mocked test | Pending |
| F09 | Vault/PostgreSQL container restart | Correct unseal requirement, preserved volumes and data | Test canary remains after restart and controlled recovery | Clean-Room | Pending |
| F10 | Non-Vault Compose mode | Existing non-Vault stack remains functional | Existing health/tests remain green | Isolated validation | Pending |

## Guardrails

1. Before every live test: verify both Compose project IDs start with `ft-cr-106-`, use only dedicated volumes/networks/TLS, and run `infra/vault/cleanroom/preflight.py` with matching local overrides.
2. Record **service health, exit codes, timestamps, test case IDs, and outcomes**; do not record tokens, Shamir shares, SecretIDs, database URLs, passwords, or Docker logs that may contain these.
3. No `docker compose down -v`, broad Docker pruning, forced deletion of volumes, or host-wide restart.
4. A live test is complete only after restoration and verifying existing Vault Raft and PostgreSQL test data remain intact.
5. The development Vault being stopped is useful as a diagnostic baseline, but is **not** authorization to modify or restart it.

## Local offline verification

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover \
  -s infra/vault/scripts -p 'test_check_readiness.py' -v
python3 -m py_compile infra/vault/scripts/check-readiness.py \
  infra/vault/scripts/test_check_readiness.py
```

CI already runs the `test_check_readiness.py` discovery command on #106 branch; do not run live Vault with secrets in public CI.
