# Issue #106 — Vault-backed development recovery runbook

> **Scope:** operator-assisted recovery of the single-node development Vault, its Runtime Agent, PostgreSQL and the NestJS/Prisma startup workflow. **Not** a production disaster-recovery, HA, auto-unseal or unattended credential-rotation procedure.
>
> **Evidence:** [Failure matrix](FAILURE-MATRIX-106.md), with isolated live validation on 2026-11-09. See also [Vault server operations](server/vault-server-README.md) and [AppRole rotation/recovery](scripts/APPROLE-ROTATION.md).
>
> **Target safety:** the executable examples below target **only** the already-created `ft-cr-106-vault` / `ft-cr-106-app` Clean-Room. The separate `ft-cr-106-nonvault` compatibility environment is **not** part of incident recovery. Do not paste these commands into the ordinary development stack or another project without separately reviewing project names, mounts, TLS files and the operational impact.

## 1. Safety rules and stop conditions

1. Identify the **exact** Compose project, service, container labels, existing Raft and PostgreSQL volumes, and TLS path *before* any state-changing command. Run the Clean-Room preflight. If identity or mounts differ, **STOP**; do not let Compose create replacement volumes.
2. Never initialize an existing Vault or run `vault operator init` to recover a sealed/stopped Vault. A normal Vault restart requires **manual unseal**, not initialization, bootstrap, or snapshot restore.
3. Do not run `docker compose down -v`, `docker volume rm`, `docker system prune`, a broad restart, forced lock removal, or an unreviewed Raft snapshot restore. Keep the existing data volumes.
4. Do not display or record root tokens, Shamir shares, SecretIDs, Vault Agent tokens, database passwords/DSNs, rendered secret files, or raw secret-bearing logs. Enter operator tokens via existing **hidden interactive prompts**. Do not enable shell tracing (`set -x`).
5. A `NOT READY` result is a **signal to investigate**, not permission to restart or rotate everything. If PostgreSQL schema/migration state, SecretID accessors or the pending rotation journal are ambiguous, **STOP and investigate** rather than retrying blindly.
6. Do not issue a new SecretID with the legacy `*-provision` helpers while a controlled rotation is pending. Do not delete `.rotation-pending.json` manually. Use the documented `rotate`, `recover`, `abort`, and `finalize` states.
7. Do not place Clean-Room secrets or live Docker fault injection in public CI. The offline readiness tests are appropriate for CI.

## 2. Select and verify the Clean-Room target

Run these commands from the repository root. The paths below are **local operator paths**, not tracked project files. Do not paste their secret contents into tickets or terminal output. If this Clean-Room no longer exists, **do not recreate it as part of incident diagnosis**.

```bash
export FT_CR_OVERRIDE_DIR="$HOME/.local/state/transcendence/issue106"
export FT_CR_TLS_DIR="$FT_CR_OVERRIDE_DIR/tls"
export FT_CR_VAULT_PROJECT="ft-cr-106-vault"
export FT_APP_COMPOSE_PROJECT="ft-cr-106-app"
export FT_APP_COMPOSE_OVERRIDE="$FT_CR_OVERRIDE_DIR/app.override.yml"
export FT_CR_PG_PASSWORD="$(cat "$FT_CR_OVERRIDE_DIR/postgres-password")"

python3 infra/vault/cleanroom/preflight.py
```

**Proceed only if every preflight guardrail passes.** For subsequent commands, establish the project-scoped Compose commands in the same Bash/Zsh session:

```bash
VAULT=(docker compose -p ft-cr-106-vault
  -f infra/vault/server/compose.yml
  -f "$FT_CR_OVERRIDE_DIR/vault.override.yml")
APP=(docker compose -p ft-cr-106-app
  -f compose.yml -f compose.vault.yml
  -f "$FT_CR_OVERRIDE_DIR/app.override.yml")
```

Verify the existing persistent mounts and service identities *without showing their contents*:

```bash
docker inspect -f '{{index .Config.Labels "com.docker.compose.project"}}|{{index .Config.Labels "com.docker.compose.service"}}|{{range .Mounts}}{{if eq .Destination "/vault/data"}}{{.Name}}{{end}}{{end}}' ft-cr-106-vault-vault-1
# Expected: ft-cr-106-vault|vault|ft-cr-106-vault_vault_data

docker inspect -f '{{index .Config.Labels "com.docker.compose.project"}}|{{index .Config.Labels "com.docker.compose.service"}}|{{range .Mounts}}{{if eq .Destination "/var/lib/postgresql"}}{{.Name}}{{end}}{{end}}' ft-cr-106-app-postgres-1
# Expected: ft-cr-106-app|postgres|ft-cr-106-app_postgres_data
```

If either container is missing, the mount differs, or more than one matching service container exists, **STOP** and identify the real state. Do **not** run a broad `up` command as a substitute for a missing instance.

## 3. Read-only triage and success criteria

Use the readiness checker first; it is designed to fail closed. Exit code `1` and `RESULT: NOT READY` are expected during an incident:

```bash
python3 infra/vault/scripts/check-readiness.py \
  --mode vault \
  --vault-project ft-cr-106-vault \
  --app-project ft-cr-106-app \
  --tls-dir "$FT_CR_TLS_DIR"

python3 infra/vault/scripts/check-readiness.py \
  --mode stack \
  --vault-project ft-cr-106-vault \
  --app-project ft-cr-106-app \
  --tls-dir "$FT_CR_TLS_DIR"

"${VAULT[@]}" ps vault
"${APP[@]}" ps vault-backend-agent postgres backend
```

`--mode vault` checks local TLS, Vault container state and initialized/sealed status; it **does not** verify AppRole validity or HA leadership. `--mode stack` additionally checks Docker health of the Runtime Agent, PostgreSQL and Backend, but **does not** directly call the Backend HTTP endpoint. A fully recovered stack should report `RESULT: READY`, and the Backend endpoint should return HTTP `200` with `status=ok`, `database=ok`:

```bash
docker exec ft-cr-106-app-backend-1 node -e '
  (async () => {
    const response = await fetch("http://127.0.0.1:3000/api/v1/health", {
      signal: AbortSignal.timeout(3000)
    });
    const body = await response.json();
    const valid = response.status === 200 &&
      body?.data?.status === "ok" && body?.data?.database === "ok";
    console.log(valid ? "PASS: Backend health HTTP 200" :
      "FAIL: Backend health does not report healthy");
    if (!valid) process.exitCode = 1;
  })().catch(() => {
    console.log("FAIL: Backend health unreachable");
    process.exitCode = 1;
  });
'
```

If the Backend container is stopped, defer the HTTP probe until after its recovery. Do not print an HTTP response body that might include unexpected diagnostics.

## 4. Recovery by symptom

### A. Vault stopped, unreachable or sealed — F01/F02/F03/F09

1. Verify Section 2 and determine whether the **existing** Vault container is stopped, running but sealed, or unreachable. Rule out a certificate/hostname problem and Docker-network failure before attempting credential rotation. A paused Vault in F03 became unavailable; unpausing restored health in the test. A *real* network timeout and malformed live HTTP responses were **not** independently injected.
2. For a **confirmed stopped** existing `ft-cr-106-vault-vault-1`, and only after verifying its Raft volume above, an authorized operator may start that same container:

   ```bash
   docker start ft-cr-106-vault-vault-1
   "${VAULT[@]}" exec vault vault status
   ```

3. For `Initialized: true`, `Sealed: true`, enter **two different** Shamir shares interactively; do not pass shares as CLI arguments:

   ```bash
   "${VAULT[@]}" exec vault vault operator unseal
   "${VAULT[@]}" exec vault vault operator unseal
   "${VAULT[@]}" exec vault vault status
   ```

   Confirm `Initialized: true`, `Sealed: false` and, once stable, active-node status. Single-node `HA Mode: active` is **not** HA resilience.
4. Re-run the Vault readiness probe, then check the Backend Agent and stack readiness. If the Agent remains unhealthy, use Section B. Do not call `vault operator init`, re-run the bootstrap blindly or restore a snapshot just because the server is sealed.
5. If Vault runs but TLS verification, API reachability or network status fails, **STOP** and diagnose that specific cause. TLS renewal/rollback is a separate, security-sensitive operation documented in [Vault server operations](server/vault-server-README.md). Do not weaken TLS validation as a workaround.

### B. Runtime Agent unhealthy, missing/stale DSN or invalid AppRole SecretID — F04

1. Check Vault unseal/TLS status and the scoped Agent Docker health. The current Agent healthcheck verifies a readable rendered DB secret and comparison with the current Vault KV value. Do **not** inspect the secret or Agent token itself.
2. If Vault is healthy and the existing AppRole credentials are valid, an operator may recreate **only** the Runtime Agent in an approved maintenance window:

   ```bash
   "${APP[@]}" up -d --no-deps --force-recreate vault-backend-agent
   "${APP[@]}" ps vault-backend-agent
   ```

   Verify `healthy`, then check the Backend HTTP endpoint and stack readiness. Avoid re-creating PostgreSQL or Vault just to fix an Agent.
3. For a **confirmed** expired, exhausted or revoked SecretID, follow [AppRole rotation/recovery](scripts/APPROLE-ROTATION.md). Normal `rotate` is for valid current credentials; explicit `recover` is for an old SecretID confirmed invalid *and* safe accessor inventory. Network errors, HTTP 401/403/5xx and an ambiguous accessor list are **not** grounds for recovery. An example of the operator-mediated Backend path (hidden token prompt; **not** a read-only operation):

   ```bash
   "${VAULT[@]}" --profile maintenance run --rm --no-deps vault-backend-rotate recover
   "${APP[@]}" up -d --no-deps --force-recreate vault-backend-agent
   "${APP[@]}" ps vault-backend-agent
   # Only after real Agent validation:
   "${VAULT[@]}" --profile maintenance run --rm --no-deps vault-backend-rotate finalize
   ```

4. If Vault AppRole user lockout was triggered by repeated failed logins, stop the failing Agent first and have an authorized operator verify the specific accessor/role before unlocking. Do **not** globally disable the lockout or blindly retry login attempts. If a rotation is pending, use the documented `abort` (candidate not installed) or `finalize` (installed and validated), not a second `rotate`.
5. Migrator and PostgreSQL Provisioner AppRoles use **different** rotation services, policies and auth volumes. Never substitute Backend credentials for them. Follow the same documented verify-before-finalize lifecycle with their **one-shot** Agents and cleanup.

### C. PostgreSQL stopped/unavailable — F05/F09

1. Verify Section 2 and that the pre-existing `ft-cr-106-app-postgres-1` has the expected `ft-cr-106-app_postgres_data` volume. Confirm the failure with scoped `"${APP[@]}" ps postgres backend` and readiness.
2. After checking the cause, an authorized operator may restart **the same stopped container** (not replace the volume):

   ```bash
   docker start ft-cr-106-app-postgres-1
   "${APP[@]}" ps postgres backend
   ```

3. Wait for PostgreSQL `healthy`, then verify the Backend HTTP endpoint returns `200` and stack readiness is `READY`. F05 demonstrated automatic Backend recovery **without a Backend restart** after PostgreSQL returned. If this fails, investigate database connectivity, role credentials and migration status instead of resetting PostgreSQL.
4. Validate data persistence using an appropriate application-specific read-only query or backup verification. In the #106 Clean-Room, the test-only marker is `issue106.f05_canary` with `id='f05'` and `marker='ft-cr-106-f05-persistence'`; it must not be assumed to exist outside this test environment.

### D. Backend stopped/unhealthy; migration/startup failure — F06/F07

1. Verify Vault is unsealed and Runtime Agent/PostgreSQL are healthy. If Backend is stopped as a result of **failed migrations**, do not just restart it: the normal startup intentionally stops Backend before migrating, and must **fail closed** on errors.
2. Confirm no concurrent migration workflow is running (see Section E). Investigate why Prisma failed. A missing schema, a failed SQL migration, or an unverified database state are **different failure classes**. Do not use `prisma migrate resolve` or edit `_prisma_migrations` without a separate reviewed recovery plan.
3. On failure, the `run-migrations.sh` exit trap **attempts** cleanup of the one-shot rendered Migrator secret; the PostgreSQL provisioner has its own cleanup trap. **Verify** cleanup actually succeeded before retrying. The operator may use the existing cleanup services once no corresponding one-shot workflow is active:

   ```bash
   "${APP[@]}" --profile vault-maintenance run --rm --no-deps -T --interactive=false vault-migrator-cleanup </dev/null
   "${APP[@]}" --profile vault-maintenance run --rm --no-deps -T --interactive=false vault-postgres-provisioner-cleanup </dev/null
   ```

4. After correcting the underlying error, and **only after** assessing any partially applied migrations, use the official entry point rather than starting Backend directly:

   ```bash
   ./infra/vault/scripts/start-backend.sh </dev/null
   ```

   This path acquires the host `flock`, stops Backend, verifies PostgreSQL credentials, renders the migration secret, runs `prisma migrate deploy`, reconciles restricted grants, cleans up and finally starts Backend. Recheck HTTP health and `RESULT: READY`.
5. **Evidence limit:** F07 tested a Prisma CLI/schema-loading failure, cleanup, fail-closed startup and recovery; it did **not** interrupt SQL mid-migration. A partially applied or indeterminate migration requires operator review, not the automatic retry above.

### E. Concurrent provisioning/migration/startup — F08

The scripts use the **same host-side `flock`**, backed by the repository's `infra/vault/scripts/run-migrations.sh` file. A second entry point should fail with exit code `1` and an `Another ... workflow is already running` message. Do **not** delete the file, force-unlock, or kill an unrelated workflow to make the warning disappear. Verify the original operation's completion/cleanup, then retry one approved workflow after the lock is released. F08 validated lock rejection/release; it did **not** run two real migrations simultaneously.

### F. Non-Vault compatibility — F10

Plain `compose.yml` uses `DATABASE_URL` rather than `DATABASE_URL_FILE` and must not be mixed with the Vault-backed Compose configuration or its private volumes. F10 was exercised with **only Backend + PostgreSQL** in the separate `ft-cr-106-nonvault` project: migrations completed, health returned `200`, and both containers were stopped with their test volume retained. This is **not** evidence for the full Frontend/Collector/Graph Engine/Nginx deployment. Do not start the separate F10 project as part of recovering the Vault-backed stack.

## 5. Post-recovery evidence checklist

Record the time (UTC), environment/project names, failure type, observable checks, sanctioned recovery action and outcome **without secrets**. A recovery can be marked complete only when applicable checks hold:

| Check | Expected result |
| --- | --- |
| Clean-Room preflight / mount identity | PASS; original Vault Raft and PostgreSQL volumes unchanged |
| Vault TLS / initialized / sealed | Certificate valid, initialized, unsealed |
| Runtime Agent | `running|healthy`; current KV-rendered secret validated by its healthcheck |
| PostgreSQL | `running|healthy`; original persistent data intact |
| Backend | `running|healthy`; `/api/v1/health` HTTP `200` with `status=ok`, `database=ok` |
| `check-readiness.py --mode stack` | `RESULT: READY`, exit `0` |
| Prisma migrations, where applicable | Expected history complete; no unreviewed failed/partial migration |
| Runtime PostgreSQL grants | No schema `CREATE` for `transcendence_app`; only application DML and read-only migration status |
| One-shot secrets | No leftover Migrator or PostgreSQL Provisioner rendered DSNs |
| Rotation transactions | No unreviewed `.rotation-pending.json`; accessors match intended installed credentials |
| Non-Vault test project | Remains isolated; stopped after testing; volume preserved if needed |

Inspect the **existence/absence** of files only; do not `cat` a SecretID, token, password, rendered database URL or sensitive Vault Agent log. For the #106 Clean-Room, the final audit verified the original canary, both completed Prisma migration records, grants, cleanup and `RESULT: READY` after all tests.

If any check fails, leave the incident **open**; record the specific safe diagnostic and escalate the unclear state to the responsible operator. Avoid claiming production readiness based on this local exercise.

## 6. Coverage boundaries and follow-up

- **F03 partial:** real Vault pause/unpause and offline mocked timeout/malformed responses passed; a separately measured live network timeout/malformed response was not injected.
- **F07 partial:** Prisma CLI failure and safe recovery passed; a crash or SQL failure *mid-migration* was not exercised.
- **F08 bounded:** `flock` contention and release were validated, but two concurrent live migrations were not attempted.
- **F09 bounded:** isolated container restarts and persistence passed; a full host reboot or Raft disaster restore was not performed.
- **F10 bounded:** only non-Vault Backend/PostgreSQL was exercised, not the full application deployment.
- **Readiness bounded:** no checks for AppRole remaining uses/TTL, full effective ACL privilege correctness or Vault HA leadership. Those require the separately documented credential lifecycle and integration procedures.

Reference: [Failure matrix / observed evidence](FAILURE-MATRIX-106.md), [Vault server operations](server/vault-server-README.md), [AppRole rotation and password lifecycle](scripts/APPROLE-ROTATION.md).
