# Vault Server — Development Infrastructure

> **Status:** Local development infrastructure, not production-ready.
> **Tracking:** [Issue #104](https://github.com/tigran-sargsyan-w/ft_transcendence/issues/104) (infrastructure), [#105](https://github.com/tigran-sargsyan-w/ft_transcendence/issues/105) (credential lifecycle), [#106](https://github.com/tigran-sargsyan-w/ft_transcendence/issues/106) (recovery).
> **Scope:** Vault Server, TLS, Raft persistence, ACL/AppRole bootstrap, and agent integration with the backend and Prisma migrations.

Vault is operated as a **single-node**, TLS-protected server backed by integrated Raft storage. Separate Vault Agents deliver PostgreSQL connection strings to the NestJS backend and the one-shot Prisma migration process. Application containers do not receive Vault root tokens.

This guide assumes **Linux with Docker Engine and Docker Compose v2**. Run all commands from the **repository root** unless stated otherwise.

## 1. Architecture and repository layout

```text
Trusted operator  --->  Vault Server (TLS + Raft + KV v2 + AppRole)
                              |
                         HTTPS / AppRole
                         /             \
              Backend Vault Agent    Migration Vault Agent
                       |                    |
               backend database_url  migrator database_url
                       |                    |
                  NestJS backend      Prisma migrate deploy
```

```text
infra/vault/
├── server/
│   ├── compose.yml             # Server, permissions, bootstrap, provisioning, maintenance
│   ├── vault.hcl                # Raft storage and TLS listener
│   ├── bootstrap.sh             # KV v2, policies, AppRoles
│   ├── tls-init.sh              # First-time local CA and server certificate
│   ├── tls-renew.sh             # Prepare a new certificate without activating it
│   ├── tls-activate.sh          # Verify, install, reload TLS, attempt rollback on failure
│   ├── tls-files.sh             # Privileged, isolated TLS file operations
│   ├── .gitignore
│   └── tls/                    # Locally generated; do not commit
├── policies/
│   ├── backend-readonly.hcl
│   └── migrator-readonly.hcl
├── agent/
│   ├── backend.hcl
│   └── migrator.hcl
└── scripts/
    ├── run-migrations.sh
    └── start-backend.sh

compose.vault.yml                # Application-side Vault integration
```

The Vault Compose file deliberately retains the project name **`transcendence-vault-poc`** for compatibility with the current external Docker network and named volumes referenced by `compose.vault.yml`. **Do not rename it without coordinating those references and migrating persistent data.**

### Separation of responsibilities

| Operation | Entry point | Behavior |
| --- | --- | --- |
| Run Vault | `infra/vault/server/compose.yml` | Persistent server, permissions setup |
| Configure Vault | `vault-bootstrap` (`setup` profile) | KV v2, policies, AppRoles; no KV values or SecretIDs created |
| Issue backend AppRole credentials | `vault-backend-provision` (`setup` profile) | Generates and writes a new SecretID |
| Issue migration AppRole credentials | `vault-migrator-provision` in `compose.vault.yml` | Generates and writes a new SecretID |
| Reconcile PostgreSQL credentials | `infra/vault/scripts/provision-postgres.sh` | Ensures roles exist and verifies Vault credentials against PostgreSQL without rotating existing passwords |
| Run migrations | `infra/vault/scripts/run-migrations.sh` | One-shot migration Agent, Prisma deploy, cleanup |
| Start backend | `infra/vault/scripts/start-backend.sh` | Stops backend, runs migrations, starts backend |

**Automation boundary:** PostgreSQL role creation, credential reconciliation, migrations, and grants are implemented through the infrastructure scripts. Initial Vault KV secret values and AppRole SecretID provisioning still require controlled operator steps. The Vault/PostgreSQL/Prisma/NestJS integration has been validated in an isolated Docker Clean-Room, including repeated migrations and backend startup. A fresh-machine installation and production deployment have not been validated; these results are not proof of production readiness.

## 2. Prerequisites and security boundaries

- Docker Engine, Docker Compose v2, Bash, OpenSSL, `flock` (util-linux), and permission to operate Docker.
- A Git-ignored `.env` containing the application stack's PostgreSQL initialization settings, as required by `compose.yml`.
- A safe place **outside Git and Docker** for the Shamir shares and initial administrative token. Never paste these into chat, tickets, shell history, container logs, or CI.
- Vault image version currently pinned to `hashicorp/vault:2.2.1`.

The Vault API is reachable over the private Docker network; its port is not published on the host. The current setup is **single node, manually unsealed, development only**, not a highly available or auto-unsealed deployment.

## 3. First-time setup (new, empty Raft volume only)

### 3.1. Generate TLS material

```bash
./infra/vault/server/tls-init.sh
openssl verify \
  -CAfile infra/vault/server/tls/ca.crt \
  infra/vault/server/tls/vault.crt
```

The script creates:

- `infra/vault/server/tls/ca.crt`, `vault.crt`, `vault.key` — ignored by Git;
- `${XDG_DATA_HOME:-$HOME/.local/share}/ft-transcendence/vault-ca/ca.key` — CA private key, outside the repository.

Server certificates are issued for **90 days**, with `DNS:vault` as the Subject Alternative Name. The local CA is initially issued for **365 days**. The initialization script refuses to overwrite existing key material. Keep a separate protected backup of the CA key: losing it prevents renewal under the existing CA.

Do **not** run `tls-init.sh` against an already configured deployment. Renew the server certificate using section 6 instead.

### 3.2. Start Vault

```bash
docker compose -f infra/vault/server/compose.yml config --quiet
docker compose -f infra/vault/server/compose.yml up -d vault
docker compose -f infra/vault/server/compose.yml exec vault vault status
```

The `vault-storage-permissions` one-shot helper prepares Raft and agent volumes and makes `vault.key` readable by the Vault process (UID 100, GID 1000, mode `0400`). A new Vault is expected to report `Initialized: false` and/or `Sealed: true` until the next steps.

### 3.3. Initialize once and protect the recovery material

**Only if `Initialized: false` and the Raft volume is genuinely new:**

```bash
docker compose -f infra/vault/server/compose.yml \
  exec vault vault operator init -key-shares=3 -key-threshold=2
```

Record the **three Shamir unseal shares** and the **initial root token** in appropriate secure storage. Two distinct shares are required to unseal this development instance. **Never initialize an already initialized Vault.**

```bash
docker compose -f infra/vault/server/compose.yml exec vault vault operator unseal
docker compose -f infra/vault/server/compose.yml exec vault vault operator unseal
docker compose -f infra/vault/server/compose.yml exec vault vault status
```

Enter different unseal shares at the hidden prompts. Wait until `Initialized: true`, `Sealed: false`, and `HA Mode: active`. The `HA Mode: active` status refers to the active node; **one node is not high availability**.

### 3.4. Configure KV v2, ACL policies, and AppRoles

```bash
docker compose -f infra/vault/server/compose.yml \
  run --rm --no-deps vault-bootstrap
```

Supply the Vault administrative token at the hidden prompt. The bootstrap:

- enables `kv/` as **KV v2** if missing;
- enables `approle/` authentication if missing;
- applies `backend-readonly`, `migrator-readonly`, and `postgres-provisioner-readonly` ACL policies;
- creates `backend-reader`, `migrator-reader`, and `postgres-provisioner-reader` if absent;
- verifies existing role parameters instead of silently overwriting an existing role;
- does **not** write database secrets or generate SecretIDs.

All three AppRoles require a SecretID (`bind_secret_id=true`), use a 7-day SecretID lifetime, a maximum of 20 logins per SecretID, a 1-hour token TTL, a 4-hour token maximum TTL, and exclude the default token policy.

Running bootstrap again is supported; note that it **reapplies ACL policies** from the repository. Review policy changes before running it on a shared deployment.

### 3.5. Create database roles and populate the two KV secrets

Prepare **distinct PostgreSQL credentials** for the application runtime and Prisma migrations, using a trusted operator process. The operator must populate the corresponding Vault KV secrets; `vault-bootstrap` does not write their values. The migration workflow invokes `provision-postgres.sh`, which runs `ensure-roles.sql` and verifies or initializes PostgreSQL role credentials. Existing active roles are verified rather than silently rotated. After Prisma migrations, `reconcile-grants.sql` applies restricted runtime privileges. The runtime role does not receive schema migration permissions.

Store the resulting **two distinct PostgreSQL DSNs** in KV v2:

| KV CLI path | Key | Consumer |
| --- | --- | --- |
| `kv/ft-transcendence/dev/backend` | `database_url` | Runtime backend Agent |
| `kv/ft-transcendence/dev/migrator` | `database_url` | One-shot migration Agent |

For a controlled manual write, open a temporary admin session:

```bash
docker compose -f infra/vault/server/compose.yml exec vault sh
```

Inside the container:

```sh
umask 077
AUTH_HOME="$(mktemp -d /tmp/vault-admin.XXXXXX)" || exit 1
export HOME="$AUTH_HOME"
trap 'rm -rf "$AUTH_HOME"' EXIT
vault login -no-print

printf 'Backend PostgreSQL URL (hidden): '
IFS= read -r -s BACKEND_DSN
printf '\n'
printf '%s' "$BACKEND_DSN" | vault kv put -mount=kv \
  ft-transcendence/dev/backend database_url=-
unset BACKEND_DSN

printf 'Migration PostgreSQL URL (hidden): '
IFS= read -r -s MIGRATOR_DSN
printf '\n'
printf '%s' "$MIGRATOR_DSN" | vault kv put -mount=kv \
  ft-transcendence/dev/migrator database_url=-
unset MIGRATOR_DSN
exit
```

This uses Vault CLI's `key=-` syntax to consume secret values from **stdin**, avoiding plaintext DSNs in command arguments. Do not enable shell tracing (`set -x`) or print the values. DSNs should use the appropriate Docker network hostname, PostgreSQL database, and URL-encoded credentials. **Never reuse the PostgreSQL superuser DSN as the backend runtime secret.**

### 3.6. Provision AppRole credentials and start the backend Agent

Provision the backend identity:

```bash
docker compose -f infra/vault/server/compose.yml \
  run --rm --no-deps vault-backend-provision
```

Provision the migration identity, using the **application** Compose files:

```bash
docker compose -f compose.yml -f compose.vault.yml \
  run --rm --no-deps vault-migrator-storage-init

docker compose -f compose.yml -f compose.vault.yml \
  run --rm --no-deps vault-migrator-provision
```

Also initialize and provision the PostgreSQL credential-reader AppRole using the application Compose files:

```bash
docker compose -f compose.yml -f compose.vault.yml \
  --profile vault-setup run --rm --no-deps vault-postgres-provisioner-storage-init

docker compose -f compose.yml -f compose.vault.yml \
  --profile vault-setup run --rm --no-deps vault-postgres-provisioner-provision
```

The AppRole provisioning commands request a privileged Vault token. They **create a new SecretID**; they are not read-only bootstrap operations. SecretID lifecycle management and revocation of old credentials remain [Issue #105](https://github.com/tigran-sargsyan-w/ft_transcendence/issues/105).

Start and check the runtime Agent:

```bash
docker compose -f compose.yml -f compose.vault.yml \
  up -d --no-deps vault-backend-agent

docker compose -f compose.yml -f compose.vault.yml \
  ps vault-backend-agent
```

Expect `healthy` only after its AppRole authentication succeeds and the correct secret file is rendered. The backend consumes `/run/secrets/backend/database_url`; the migration process uses its **separate**, one-shot Agent and `/run/secrets/migrator/database_url`.

### 3.7. Start migrations and the backend

```bash
./infra/vault/scripts/run-migrations.sh
./infra/vault/scripts/start-backend.sh
```

Normally use **`start-backend.sh`** as the backend startup entry point: it already calls the migration workflow, so you do **not** need to run both commands every time. Both scripts use a host-side `flock` guard. The migration workflow cleans up its rendered migration secret on normal exit and handled interruption.

**Verified application integration:** Backend `/api/v1/health` reads `public._prisma_migrations`. The required read-only `SELECT` grant is applied by `reconcile-grants.sql` after Prisma migrations. This was verified in the isolated Clean-Room with a restricted runtime PostgreSQL role, without granting schema ownership or migration privileges to the backend. See [Issue #92](https://github.com/tigran-sargsyan-w/ft_transcendence/issues/92).

## 4. Routine operations

```bash
# Vault status
docker compose -f infra/vault/server/compose.yml exec vault vault status

# Runtime Agent health
docker compose -f compose.yml -f compose.vault.yml ps vault-backend-agent

# Compose validation
docker compose -f infra/vault/server/compose.yml config --quiet
docker compose -f compose.yml -f compose.vault.yml config --quiet
```

After any Vault **container restart**, the Shamir-sealed server must be unsealed again with two different shares. Do not reinitialize it. If an Agent is unhealthy, inspect its healthcheck and logs **without printing tokens or rendered secrets**. A `400 invalid role or secret ID` AppRole error can indicate an expired, exhausted, or revoked SecretID; use the controlled provisioning procedure, then recreate the affected Agent if necessary.

**Never run `docker compose down -v` against the Vault project** unless intentionally destroying all persistent Vault data and agent state.

## 5. Raft snapshots and recovery

Take protected Raft snapshots before infrastructure changes. Keep them outside Git and outside the running Vault data volume, with permissions restricting access to administrators. Snapshots contain sensitive Vault state and are **not** substitutes for the required Shamir shares.

The standard Vault CLI operation, with an authorized administrative token, is:

```sh
vault operator raft snapshot save /tmp/raft-backup.snap
vault operator raft snapshot inspect /tmp/raft-backup.snap
```

If invoked inside the Vault container, securely copy the snapshot to a protected host directory, verify its checksum against the container copy, and remove the temporary container file. Do **not** upload unencrypted snapshots to issues or CI artifacts.

Restoring a snapshot is a **separate, potentially destructive recovery operation**. Follow a reviewed recovery runbook and [Issue #106](https://github.com/tigran-sargsyan-w/ft_transcendence/issues/106); do not use `snapshot restore` as a routine startup step.

## 6. TLS certificate renewal (same CA)

The local Vault certificate lasts 90 days. **Renew it before expiration.** The existing CA private key is stored outside Git at:

```text
${XDG_DATA_HOME:-$HOME/.local/share}/ft-transcendence/vault-ca/ca.key
```

Prepare the next certificate and private key without changing the live files:

```bash
./infra/vault/server/tls-renew.sh
```

The command prints a candidate path such as `infra/vault/server/tls/.renew.XXXXXXXX`. Capture only its **directory name**, including `.renew.`.

```bash
./infra/vault/server/tls-activate.sh check .renew.XXXXXXXX
./infra/vault/server/tls-activate.sh activate .renew.XXXXXXXX
```

Replace `.renew.XXXXXXXX` with the **actual** directory name generated on your machine. `check` validates certificate trust, hostname, matching private key, and the current live connection; `activate` backs up the previous pair, installs the candidate, sends `SIGHUP`, verifies the live certificate fingerprint, and checks Vault and the Runtime Agent.

The active `vault.key` is owned by UID `100` and mode `0400`. The privileged `vault-tls-files` one-shot container performs file replacement in a network-isolated maintenance profile; **do not chmod it to world-readable**. A successfully installed old pair is retained at:

```text
infra/vault/server/tls/.backups/backup-<timestamp>-<pid>/
    vault.crt
    vault.key
```

Backup directories and keys are root-owned and restricted. Use `sudo ls` or a restricted maintenance container to inspect their **names and permissions**, not the key contents.

### Manual TLS rollback

If activation or connectivity fails, **do not immediately restart or reinitialize Vault**. Locate the specific previously created `backup-*` directory, then, after checking its identity:

```bash
docker compose -f infra/vault/server/compose.yml \
  run --rm --no-deps -T vault-tls-files rollback backup-ACTUAL_NAME

docker kill --signal=HUP transcendence-vault-poc-vault-1
```

Replace `backup-ACTUAL_NAME` with the actual backup directory name. Verify the live TLS fingerprint, `vault status`, and Runtime Agent health afterward. A failed or interrupted replacement might require manual operator intervention; do not treat rollback as guaranteed under every failure mode.

**CA renewal/rotation is not yet implemented.** `tls-renew.sh` refuses a 90-day server certificate when the CA will expire within that period. Changing the trusted CA also requires updating agent trust and validating the entire chain; rotating a server certificate signed by the same CA is a different procedure.

## 7. Security and known limitations

- Development-only single-node Raft; not production HA.
- Manual Shamir initialization/unseal; no production-grade auto-unseal.
- Administrative provisioning currently requires a root/admin token in an interactive session; a narrowly scoped provisioning identity is future work.
- AppRole SecretID TTL is **168 hours**, with **20 uses**; credentials may need reprovisioning before expiry or on exhaustion. Automated rotation and revocation remain separate work.
- Persistent Docker volumes contain authentication and rendered secret material; Docker host administrators can access them.
- TLS rotation succeeded locally via `SIGHUP` without container restart, but interrupted-file replacement and rollback failure scenarios are not comprehensively tested.
- PostgreSQL role creation, credential reconciliation, and grants are automated and were tested in an isolated Clean-Room. Initial Vault KV values and AppRole provisioning still require operator setup; a fresh-machine installation remains untested.
- Backend health permissions, one-shot migration Agent, repeated Prisma migrations, and the startup workflow passed Clean-Room integration tests. Production deployment, disaster recovery, and complete credential lifecycle handling remain separate work.
- Certificates, CA keys, Vault root tokens, SecretIDs, Raft snapshots and Shamir shares **must not be committed or uploaded**.

## 8. Related documentation

- The original isolated Vault POC is preserved in Git history. Its commands and configuration are superseded by the development Vault Server.
- [Vault secret delivery contract](../../../docs/architecture/vault-secret-delivery.md) — original proposal; may be stale relative to merged implementation.
- [ADR 0003](../../../docs/adr/0003-vault-secret-delivery.md) — decision record.
- [Issue #92 — application integration](https://github.com/tigran-sargsyan-w/ft_transcendence/issues/92)
- [Issue #104 — development infrastructure](https://github.com/tigran-sargsyan-w/ft_transcendence/issues/104)
- [Vault KV CLI: writing values through stdin](https://developer.hashicorp.com/vault/docs/commands/kv/put)
- [Vault TCP listener: certificate reload on SIGHUP](https://developer.hashicorp.com/vault/docs/configuration/listener/tcp)
