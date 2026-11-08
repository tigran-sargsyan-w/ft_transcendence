# Operator-controlled AppRole SecretID rotation (#105, phase A)

This initial implementation is **manual**, not a timer or a privileged long-lived issuer. It supports all three AppRoles and intentionally separates installation from revocation so a real Agent can be checked before old credentials are invalidated.

## Security boundaries

- Vault must already be initialized and manually unsealed.
- Run each helper interactively. It prompts for a Vault operator token with echo disabled. The operator must be able to manage AppRole SecretIDs **and** revoke the temporary verification token via auth/token/revoke; the three AppRole identities retain their original read-only ACLs. Never put tokens or database URLs in Git, Compose, environment variables, CI or shell arguments.
- Each temporary helper has access to **only its own** role's authentication volume, the private Vault network, the CA certificate and the read-only Python script.
- Do not run the older *-provision services concurrently; those older services do not use the new rotation lock or journal.
- Both initial candidate validation and finalize perform an AppRole login, consuming **one SecretID use each**. Existing roles have a 20-use limit.
- Do not use Docker volume removal or broad cleanup. Preserve the original Raft and AppRole volumes.

## 1 — Issue, verify and install (does not revoke old SecretID)

Backend:

~~~sh
docker compose -f infra/vault/server/compose.yml \
  --profile maintenance run --rm --no-deps vault-backend-rotate rotate
~~~

Prisma migrator:

~~~sh
docker compose -f compose.yml -f compose.vault.yml \
  --profile vault-maintenance run --rm --no-deps vault-migrator-rotate rotate
~~~

PostgreSQL provisioner:

~~~sh
docker compose -f compose.yml -f compose.vault.yml \
  --profile vault-maintenance run --rm --no-deps vault-postgres-provisioner-rotate rotate
~~~

The helper issues a new SecretID, captures its accessor, validates an AppRole login with the expected *single* read-only policy, reads all required KV paths without printing their values, then atomically replaces each credential file. A protected **.rotation-pending.json** journal stores accessors and a SHA-256 fingerprint of the previous high-entropy SecretID, never plaintext SecretIDs or tokens. An existing old credential remains valid in Vault until finalized.

These commands also work for initial provisioning when there is no previous SecretID.

## 2 — Verify the actual Agent before revoking its old credential

**Backend:** in a maintenance window, force the runtime Agent to read its new credentials and check health:

~~~sh
docker compose -f compose.yml -f compose.vault.yml \
  up -d --no-deps --force-recreate vault-backend-agent
docker compose -f compose.yml -f compose.vault.yml ps vault-backend-agent
~~~

Also verify the application's /api/v1/health endpoint.

**Migrator:** run its existing one-shot Agent (does not apply Prisma migrations), then remove the rendered secret:

~~~sh
docker compose -f compose.yml -f compose.vault.yml \
  --profile vault-migration run --rm --no-deps vault-migrator-agent
docker compose -f compose.yml -f compose.vault.yml \
  --profile vault-maintenance run --rm --no-deps vault-migrator-cleanup
~~~

**Provisioner:** run its existing one-shot Agent and clean up rendered credentials:

~~~sh
docker compose -f compose.yml -f compose.vault.yml \
  --profile vault-setup run --rm --no-deps vault-postgres-provisioner-agent
docker compose -f compose.yml -f compose.vault.yml \
  --profile vault-maintenance run --rm --no-deps vault-postgres-provisioner-cleanup
~~~

Inspect status only, never print the rendered connection strings or authentication files.

## 3 — Finalize

Repeat the appropriate role-specific Compose command from step 1, replacing the final **rotate** argument with **finalize**. The helper verifies the installed SecretID, then revokes the previous SecretID **by accessor**, and removes the protected journal. Revoking a SecretID does not itself revoke already-issued Vault tokens.

## Recovery / interrupted operation

- If the prior SecretID is expired/exhausted (Vault returns HTTP 400/404), normal **rotate** fails closed. After inspection, use **recover**. Network failures, 401/403 authorization errors and Vault 5xx are **never** treated as expiration. Unknown previous accessors must be audited by an operator.
- If issuing/verifying a candidate failed **before installation**, run **abort** to revoke the unused candidate. Abort uses the protected previous-secret fingerprint so it can abort with an expired old SecretID; it refuses if the installed file changed and the candidate may be in use.
- If installation succeeded but finalization failed or was interrupted, fix the cause, validate the actual Agent, then retry **finalize**. Missing old accessors can be confirmed absent before treating an already-completed revocation as successful. Authorization, network, and ambiguous failures still stop the operation.
- If the old SecretID vanishes/changes after staging, an operator may have to reconcile that condition manually. If writing the initial journal fails, the helper tries to revoke the untracked candidate immediately. Never delete the journal as a shortcut or silently fall back to an old secret.
- The legacy provisioning helpers bypass the new lock. Do not run them while a rotation is pending.

## Offline tests and limits

~~~sh
python3 -m unittest discover -s infra/vault/scripts -p 'test_rotate_approle.py' -v
~~~

Offline tests cover role boundaries, initialization, staging, denied/network recovery, operator-mediated validation-token revocation, durable journal failures, interrupt/retry, abort, and idempotent old SecretID revocation. No real Vault is initialized in CI. **A separate isolated Vault Clean-Room integration test must be passed before treating this as production-ready**. Automated scheduling, delegated issuer policies, full password rotation for PostgreSQL, and unattended Vault unseal are deliberately excluded from this first phase.
