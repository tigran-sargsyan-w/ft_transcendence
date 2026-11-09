# Vault secret delivery contract — v1 (proposal)

> **Status:** Architecture proposal only. No Vault service or application integration is implemented by this document. Team review is required before runtime changes.

- Owner: Tigran (Vault/Agent, TLS, ACL and Compose security boundaries)
- Application interface: Nathan (NestJS/Prisma credential loading and startup)
- Tracking: [Issue #92](https://github.com/tigran-sargsyan-w/ft_transcendence/issues/92)
- Decision record: [ADR 0003](../adr/0003-vault-secret-delivery.md)

## Current state (before Vault)

- `compose.yml` passes `POSTGRES_PASSWORD` from a Git-ignored `.env` into `postgres` and constructs a backend `DATABASE_URL` from it.
- The backend Dockerfile uses `npx prisma migrate deploy && exec npm run start`. The backend expects a `DATABASE_URL` environment variable at startup.
- `GRAPH_ENGINE_URL` is a nonsecret internal service address; session cookies use randomly generated opaque tokens, not a shared JWT signing secret.
- No Vault, Vault Agent, KV mount or secret-delivery volume exists in the current `main` runtime. The existing development stack must remain runnable while the isolated POC is prepared.

## Trust boundaries

```text
Trusted operator (bootstrap / unseal / provision roles)
                     |
            Vault server (TLS)
        KV v2 + policies + Raft
                     |
        machine AppRole auth
                     |
         dedicated Vault Agent
                     |
     restricted private secret file
                     |
        specific service consumer
   (NestJS / Prisma; later PostgreSQL bootstrap)
```

Only Vault Agents receive AppRole credentials. Backend does **not** get a Vault root token or unseal keys, and agents for different consumers do not share the same policy or delivered file volume. For a local POC a single Agent + fake consumer is sufficient; separate real-consumer agents are a later phase.

## Proposed KV v2 paths and consumers

| Logical CLI path | Intended consumer | Example key (placeholder only) | Access |
| --- | --- | --- | --- |
| `kv/ft-transcendence/dev/poc-demo` | isolated POC agent | `demo_value` (fake string) | POC AppRole: read only |
| `kv/ft-transcendence/dev/backend` | NestJS/Prisma backend | `database_url` (future app-user DSN) | backend AppRole: read only |
| `kv/ft-transcendence/dev/postgres-bootstrap` | DB initialization only | `postgres_password` (future bootstrap secret) | DB bootstrap AppRole: read only |

KV v2 is mounted at `kv/`; the actual API/ACL path for the backend secret is `kv/data/ft-transcendence/dev/backend`. Secret path names and example keys are a **proposed contract**, not claims that they have already been provisioned. Do not put example real passwords, real app secrets or initialization keys in documentation.

### Example minimal ACL (illustrative; not deployed)

```hcl
path "kv/data/ft-transcendence/dev/poc-demo" {
  capabilities = ["read"]
}
```

No wildcard access, no `list`, no `create`, no `update`, no `delete`, no `sudo` on the POC policy. AppRoles for backend and database bootstrap must be similarly restricted to their own exact paths. SecretID and privileged operator actions are excluded from these roles.

## Agent file interface (not implemented yet)

- Vault Agent authenticates with a role-specific AppRole and reads only the explicitly authorized KV value.
- Agent renders the result to an **untracked, access-restricted runtime file**, not a repository file or a plaintext Compose variable.
- Proposed contract for a future backend consumer: `/run/secrets/backend/database_url` contains **only the connection URL** (not JSON or a shell fragment). A backend-controlled startup wrapper or file-aware configuration must load it *before* `prisma migrate deploy` and NestJS initialize. Validate URI encoding and keep its value out of logs.
- Proposed contract for PostgreSQL initialization: `/run/secrets/postgres/bootstrap_password`, consumed only by the PostgreSQL bootstrap process if the image supports an appropriate file-based input. Check exact image entrypoint behavior before implementation.
- Permissions: file readable by only the intended service UID/GID; parent directory private; use a dedicated runtime mount per consumer, ideally memory-backed when possible, and no broad host bind mount. Validate permissions inside each actual container.
- A Vault Agent **may refresh a rendered file** after a KV v2 change, but applications must explicitly reload or restart when their credentials change. This contract does not promise live rotation.

**Important:** if a wrapper exports `DATABASE_URL` to launch Prisma/Node, the credential can still exist in that process's environment. The initial goal is to avoid committing or embedding it in static Compose/container metadata and to control access, **not** to claim the runtime environment can never expose a credential. Investigate file-aware application/migration clients if stronger isolation becomes necessary.

## Authentication and bootstrap lifecycle

1. Operator starts a Vault server in **normal** mode with a persistent Raft data volume and configured TLS. Avoid a publicly reachable listener; administrative access is a temporary, controlled local operation.
2. First-time operator initializes Vault and securely stores unseal shares/root bootstrap token outside the repository and container logs. An initialized but **sealed** server cannot serve secrets; after a restart, manual unseal is necessary with the proposed local Shamir design.
3. Operator enables KV v2, configures a narrow POC policy and AppRole, writes a clearly fake POC value, and delivers AppRole credentials through a secure out-of-band mechanism. Treat `SecretID` as sensitive; response wrapping is a later POC check.
4. Agent uses verified TLS and AppRole, creates the private test file and exposes **only the file** to the fake consumer. Root token and unseal shares are never passed to the agent or app.
5. The POC tests missing credentials, forbidden path and sealed Vault. No existing application container requires Vault during this first phase.
6. For future app integration, define an explicit readiness gate: required secret file must exist and pass validity/permissions checks **before** Prisma migration and NestJS process start. Do not silently fall back to a hard-coded password.

### Recovery and restart semantics

- Persistent Raft volume survives restarts but does not remove unseal requirements. A recovered POC must intentionally unseal and prove its fake value remains available.
- A fresh start with no valid secret file must fail closed and produce a **nonsecret** diagnostic message.
- An already-running application may keep a previously loaded password during a Vault outage; define behavior explicitly during backend integration rather than claiming automatic shutdown.
- Do not initialize Vault repeatedly on every `docker compose up`. Never automate unseal by storing its shares or root token in tracked Compose or startup scripts.
- Keep the project's single-command application startup requirement visible: a first-time Vault initialization/unseal is a separate documented prerequisite until a secure bootstrap/auto-unseal strategy is approved.

## Phased implementation / validation

### Phase 0 — Docs only

- Review this contract and [ADR 0003](../adr/0003-vault-secret-delivery.md).
- Confirm exactly which interface changes are needed with Nathan before touching his application startup.

### Phase 1 — isolated fake-secret POC

- Normal-mode Vault starts from a pinned image; Raft persistent data and TLS trust are configured.
- Test KV v2 fake value, narrow read policy and forbidden-path denial.
- Vault Agent renders to a private file without writing credentials to tracked files, Compose variables or logs.
- Test restart -> sealed -> controlled unseal -> same fake value.
- Test missing/expired AppRole SecretID and failed template rendering.

### Phase 2 — controlled application integration

- Agree application DB user vs migrations/bootstrap role; avoid giving backend the PostgreSQL administrator's credentials.
- Prove startup with a file-delivered DSN and check Prisma migrations against an empty database.
- Verify existing PostgreSQL data/volumes are not modified unexpectedly. `POSTGRES_PASSWORD_FILE` should be verified against the selected official image; changing an initialization password setting does not itself rotate an already-initialized database account.
- Add separate Agent identity and runtime mount for PostgreSQL bootstrap only if justified.

### Phase 3 — security evidence

- Verify Docker/host exposure, Vault policy denials, TLS certificate verification and file access by unexpected containers.
- Redact credentials from CI, shell history, logs, screenshots, `docker inspect` checks and test output.
- Document limitations: local single-node, manual unseal, no automatic application reload and no dynamic database leases in v1.

## Explicitly outside scope

- ModSecurity, OWASP CRS, Socket.IO gateway or issue #43 implementation.
- Production HA cluster, auto-unseal without a trusted external key source, production backups, PKI engine.
- Dynamic PostgreSQL credentials, automatic rotation, auth/session feature changes.

## Official references

- [Vault Integrated Storage (Raft)](https://developer.hashicorp.com/vault/docs/configuration/storage/raft)
- [Vault KV v2](https://developer.hashicorp.com/vault/docs/secrets/kv/kv-v2)
- [Vault AppRole](https://developer.hashicorp.com/vault/docs/auth/approle)
- [Vault Agent AppRole auto-auth](https://developer.hashicorp.com/vault/docs/agent-and-proxy/autoauth/methods/approle)
- [Vault Agent templates](https://developer.hashicorp.com/vault/docs/agent-and-proxy/agent/template)
- [Vault seal/unseal](https://developer.hashicorp.com/vault/docs/concepts/seal)
