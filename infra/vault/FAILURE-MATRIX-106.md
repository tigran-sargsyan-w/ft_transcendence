# Issue #106 — Vault / Agent / PostgreSQL failure matrix

**Scope:** local development reliability, not production HA or auto-unseal. All fault injection must run in a newly provisioned, isolated `ft-cr-106-*` Clean-Room using unique Compose projects, Docker volumes, and TLS files. **Never inject failures into the ordinary development projects, the prior `ft-cr-105-*` environments, or their Raft/PostgreSQL data.**

## Baseline / existing evidence

- A **non-injected** local diagnostic on 2026-11-09 found the ordinary development Vault stopped, the Runtime Agent unhealthy, and PostgreSQL/Backend exited. Its TLS certificate verified locally (expiry 2027-02-01). Readiness correctly reported `NOT READY`. This observation is **not** evidence of a successful recovery test and did not authorize changing that environment.
- All live fault injection described below was performed on 2026-11-09 in separate Clean-Room projects: `ft-cr-106-vault` and `ft-cr-106-app`. The separate non-Vault compatibility test used `ft-cr-106-nonvault`. The Clean-Room Vault TLS certificate verified through 2027-02-07.
- `check-readiness.py --mode vault` checks the Vault server, initialization/seal state and local TLS only; it does **not** verify AppRole SecretID validity, migration readiness or HA active-node status. `--mode stack` additionally checks the Runtime Agent, PostgreSQL and Backend Docker health. It does not directly exercise the Backend HTTP endpoint; that endpoint was tested separately in F05/F10.
- The 15 `test_check_readiness.py` unit tests passed after live testing. Offline tests cover Docker unavailability, ambiguous containers, invalid Vault responses, API timeouts, uninitialized/sealed Vault, missing/expiring TLS and unhealthy dependencies.

## Failure matrix — observed results (2026-11-09)

`PASS` applies to the specifically tested behavior, not every possible failure variation. `PARTIAL` explicitly identifies remaining untested behavior.

| ID | Failure / condition | Observed evidence | Recovery criterion / limitations | Test type | Status |
|---|---|---|---|---|---|
| F01 | Vault server stopped or Docker unavailable | Stopped test Vault caused `NOT READY`; checker did not start, initialize or unseal it. Docker-unavailable behavior passed offline tests. | Controlled Vault restart and manual unseal restored readiness; a real Docker daemon outage was **not** injected. | Offline + Clean-Room | **PASS** (Vault stop/recovery; Docker outage mocked) |
| F02 | Vault running but sealed | Live sealed Vault failed readiness with `Vault API: sealed`. | Manual unseal restored Vault availability and subsequent Agent health. No auto-unseal was attempted. | Offline + Clean-Room | **PASS** |
| F03 | Vault API timeout or unexpected response | Live Vault pause made readiness `NOT READY` and Agent unhealthy; unpause restored health. Timeout and invalid response handling passed mocked tests. | Live pause/recovery passed; **no separately measured live network timeout or malformed HTTP response** was injected. | Offline + Clean-Room | **PARTIAL** |
| F04 | Runtime Agent unhealthy, missing rendered secret, or invalid SecretID | Agent became unhealthy during Vault pause and recovered; cold-start while Vault paused recovered after unpause. A revoked Backend SecretID prevented Agent authentication. | Vault AppRole lockout was explicitly cleared, credentials recovered through `rotate recover` / `finalize`, and Agent returned healthy. Exactly one active installed SecretID accessor was verified. | Offline + Clean-Room | **PASS** (tested Agent/SecretID paths) |
| F05 | PostgreSQL stopped/unavailable | Live stop caused Backend `/api/v1/health` HTTP `503`, Backend Docker `unhealthy`, and stack `NOT READY` (exit `1`); Vault Agent remained healthy. | Restarting the **same** PostgreSQL container restored Backend HTTP `200`, Docker `healthy` and stack `READY` **without restarting Backend**. F05 canary and both Prisma migration records survived. | Offline + Clean-Room | **PASS** |
| F06 | Backend stopped, starting or unhealthy | Stopped Backend produced `NOT READY` (exit `1`); Agent and PostgreSQL stayed healthy. Starting/unhealthy behavior also covered by offline checks and F05. | `docker start` restored Backend health and stack `READY` without container recreation or rerunning migrations. | Offline + Clean-Room | **PASS** (live stop/recovery) |
| F07 | Prisma migration fails or is interrupted | Injected a Prisma CLI error using a nonexistent schema during the real `start-backend.sh` workflow. Migration/startup failed closed, Backend stayed stopped, readiness was `NOT READY`, and temporary Migrator/Provisioner DSNs were absent. | Once the injection was removed, the official startup workflow succeeded and returned `READY`; grants, canary and migration history remained intact. **No mid-SQL migration interruption or partially applied migration was tested.** | Clean-Room fault injection | **PARTIAL** |
| F08 | Concurrent `start-backend.sh` / `run-migrations.sh` | While an exclusive `flock` was held, both scripts rejected concurrent execution with exit code `1`; a Docker-command guard confirmed neither reached Docker operations. | Lock could be acquired after release; normal workflows had independently completed successfully. This was **lock contention testing**, not two live migrations racing. | Clean-Room safe contention test | **PASS** (lock rejection/release) |
| F09 | Vault / PostgreSQL container restart | Restarted isolated Vault and manually unsealed it; Raft/KV canary persisted. PostgreSQL Compose restart preserved its volume and F05 canary. | Both services recovered with original isolated volumes and persisted test data; no data volume deletion or reset was performed. | Clean-Room | **PASS** |
| F10 | Non-Vault Compose mode | Separate `ft-cr-106-nonvault` project ran **only** Backend + PostgreSQL using `DATABASE_URL` (no Vault Agent); health returned HTTP `200` and two Prisma migrations completed. | Non-Vault containers were stopped afterward, their PostgreSQL volume retained, and the original Vault-backed stack remained `READY`. **Frontend, Collector, Graph Engine and Nginx were not tested.** | Isolated non-Vault compatibility | **PASS** (Backend + PostgreSQL only) |

## Live test evidence and final restoration

- **Isolation:** The Clean-Room preflight passed before live injection. Projects had independent identities, volumes, internal Vault networking and TLS files; no published ports or Docker socket were exposed in the tested Vault-backed stack. No ordinary development or `ft-cr-105-*` containers/volumes were targeted.
- **Credential lifecycle:** Backend, Migrator and PostgreSQL Provisioner AppRoles were validated against their scoped Vault policies. Migrator and Provisioner initial SecretID issuances were followed by real Agent checks and `finalize`. Installed accessors were checked against the sole active SecretID for each of those roles. Revoked Backend credentials were recovered and the Backend Agent became healthy again. No SecretID, token, password, connection string or sensitive Docker Agent log is recorded here.
- **Database provisioning:** Vault-backed credentials were delivered to the one-shot Provisioner, and `transcendence_app` / `transcendence_migrator` gained password-based TCP/SCRAM login without PostgreSQL administrative privileges. Two Prisma migrations (`20260920161343_init_user`, `20260928163702_add_sessions`) completed successfully. Backend retained data-table DML and read-only access to `_prisma_migrations`, without schema `CREATE`; the Migrator retained schema `CREATE`.
- **Recovery checks:** F05 verified Backend HTTP `503` during PostgreSQL outage and HTTP `200` after recovery; F06 verified stop/restart readiness; F07 verified fail-closed startup, automatic temporary-DSN cleanup and successful official recovery; F08 verified exclusive locking without allowing Docker mutation from blocked contenders. The test canary `issue106.f05_canary` survived outage/restart and migration workflows.
- **End-of-run audit:** `ft-cr-106-app` Backend, PostgreSQL and Runtime Agent were `running|healthy`, Vault was initialized and unsealed, and the full-stack checker returned `RESULT: READY`. Both Prisma migration records were finished, the F05 canary and restricted grants were intact, Migrator/Provisioner temporary rendered DSNs were absent, and neither AppRole had a pending rotation journal. The F10 Backend/PostgreSQL containers were stopped with their data volume preserved. The final local verification reported **15/15 readiness unit tests passed**, correct branch and a clean working tree **before this documentation update**.

## Remaining limitations / follow-up

- **F03:** A paused Vault and mocked timeout/invalid responses do not constitute a controlled live network timeout test. Capture one separately only if required, while preserving the same Clean-Room safety controls.
- **F07:** The injected failure occurred at the Prisma CLI/schema-loading stage. A process interruption or a database error during SQL migration execution, plus its associated recovery strategy, remains untested.
- **F08:** The contention test exercised both workflow entry points while the lock was already held. It did not execute two competing real migration operations; successful lock reuse after release was validated.
- **F10:** Compatibility validation covered Backend and PostgreSQL only, not the full non-Vault application deployment.
- **Readiness scope:** The checker reports Docker health and Vault seal/TLS status, not SecretID TTL/remaining uses, full permission correctness, Vault HA leadership or production readiness. Separate Agent, provisioning, migration and HTTP checks supply the stronger evidence noted above.

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
