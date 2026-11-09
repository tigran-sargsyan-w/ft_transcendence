# ADR 0003: HashiCorp Vault for application secret delivery

- Status: Proposed
- Date: 2026-10-24
- Owner: Tigran (infrastructure/security); Nathan owns NestJS/Prisma integration
- Tracking: [Issue #92](https://github.com/tigran-sargsyan-w/ft_transcendence/issues/92)

## Context

The project currently passes `POSTGRES_PASSWORD` from a local `.env` into PostgreSQL and interpolates that password into the backend's `DATABASE_URL` in `compose.yml`. The backend's startup command runs `prisma migrate deploy` before NestJS starts. Existing `.env` hygiene satisfies the basic security baseline, but the planned Cybersecurity major module also requires HashiCorp Vault for managed, encrypted, isolated secrets alongside a hardened WAF.

We need a design that Tigran can test independently before coordinating application startup changes with Nathan. Vault is **not** a prerequisite for Socket.IO, ModSecurity or the Docker collector; integration must not unexpectedly break the current development stack.

## Considered options

### A. Continue with Git-ignored `.env` files only

Simplest development workflow and no additional service, but lacks Vault-managed authorization, auditability and centralized secret lifecycle. Keep as the existing baseline until migration is proven.

### B. Vault KV v2 + AppRole + Vault Agent file rendering (proposed)

Separates secret storage, machine authentication and application consumption; supports least-privilege ACLs and a testable file handoff. Costs an additional runtime, TLS, operator bootstrap/unseal, and file/rotation lifecycle.

### C. Let NestJS call Vault directly via SDK

Would avoid file handoff but couples application code and availability directly to Vault-specific authentication, policies and client lifecycle. Not selected for v1.

### D. Vault database secrets engine with dynamic PostgreSQL users

Provides leased credentials but requires decisions on Prisma migrations, privileged bootstrap accounts, connection management and rotation. Defer until static-secret integration is understood and tested.

## Proposed decision

Adopt option **B** in incremental phases, subject to team review:

1. **Server:** one Vault instance in normal server mode (never `-dev`) with persistent Integrated Storage (Raft). Single-node mode is suitable for local POC only, not production HA. Pin image versions when implemented.
2. **Transport and access:** internal service-only network with verified TLS between Vault and its agents. No permanent public Vault UI/API exposure or TLS-verification bypass.
3. **Secrets:** explicitly enable **KV v2** at mount `kv/`. Initial namespace is `ft-transcendence/dev/`; keep environments separate.
4. **Machine identity:** a distinct **AppRole** per consumer with short-lived tokens and narrowly scoped Vault ACL policies. Protect the `SecretID` as a credential, and investigate response wrapping for provisioning. Do not distribute a root token to services.
5. **Delivery:** per-consumer **Vault Agent** renders only the authorized values to private runtime files. The application does not require a Vault SDK. Files must be accessible only to the intended service user/group; do not mount the same secret directory broadly.
6. **Stage 1:** demonstrate end-to-end delivery with an obviously fake secret, **without modifying NestJS, PostgreSQL or current Compose runtime behavior**.
7. **Stage 2 (jointly reviewed):** agree backend/Prisma start order, file contract and distinct database application vs bootstrap credentials with Nathan. Do not silently switch current `DATABASE_URL` semantics.

See [Vault secret delivery contract v1](../architecture/vault-secret-delivery.md) for precise proposed paths, startup boundaries and validation.

## Security and operations constraints

- Never commit or log unseal shares, root token, AppRole SecretIDs, real credentials, rendered files, or TLS private keys.
- The initial local POC uses **manual, documented initialization and unseal** with key material handled outside the repository. Normal mode with Shamir seal does not automatically unseal on restart; automatic unseal requires a separately trusted KMS/HSM/transit service and a later decision.
- Store Raft data persistently and isolate Vault/Agent state. Raft persistence does not replace backups, protected recovery material or high availability.
- For KV v2, ACL reads target `kv/data/<path>`, not the friendly CLI path `kv/<path>`. Read-only app roles must not list, write or delete secrets unless justified.
- Treat rendered files as sensitive. No debug output of file contents; check permissions/ownership, mount isolation and lifecycle. A runtime startup wrapper may still need to set an environment variable for Prisma; that exposure must be documented rather than claimed eliminated.
- Define explicit startup behavior when Vault is sealed, secrets are absent or file rendering fails. A running app may keep an already loaded credential during a Vault outage; fail closed on a **new** startup without a valid secret.
- Updating a KV value or Agent-rendered file does not automatically reconfigure NestJS/Prisma/PostgreSQL. Rotation and restart/hot-reload semantics are deferred to integration design.

## Consequences

### Positive

- Clear ownership boundary: Tigran provisions Vault, agents, policies and delivery; Nathan controls how NestJS/Prisma reads the delivered secret.
- Can prove ACL denials, safe delivery and persistence independently before touching real passwords.
- Avoids coupling app business code to Vault client libraries; supports later dynamic secrets and wider platform hardening.

### Negative / trade-offs

- Local bootstrap/unseal and AppRole provisioning introduce extra operator steps. The subject's single-command deployment requirement needs a documented strategy for a **previously provisioned/unsealed environment**; a fresh sealed Vault cannot be assumed to work unattended.
- More services, secure volumes, TLS trust, protected initialization material and failure modes to test.
- No claim of production readiness, high availability or automatic secret rotation from the initial POC.

## Open integration decisions

- How to provision a separate least-privilege PostgreSQL application user and migration role, without handing the backend PostgreSQL bootstrap/admin credentials.
- Whether PostgreSQL's supported `POSTGRES_PASSWORD_FILE` initialization path fits the selected image and how an existing initialized volume is migrated; initial database password changes are not applied automatically merely by changing the startup variable.
- How to deliver the backend database URL to both `prisma migrate deploy` and NestJS (file-aware startup wrapper vs explicit application file support); validate permissions and runtime environment exposure.
- How to handle agent readiness, sealed Vault on local restart, AppRole SecretID refresh, certificate trust, and runtime rotation semantics.

## Related

- [Issue #92 — Vault implementation](https://github.com/tigran-sargsyan-w/ft_transcendence/issues/92)
- [Secret delivery contract v1](../architecture/vault-secret-delivery.md)
- [Security baseline](../architecture/security-baseline.md)
- [Responsibility matrix](../team/responsibility-matrix.md)
- [Vault integrated storage](https://developer.hashicorp.com/vault/docs/configuration/storage/raft)
- [Vault KV v2](https://developer.hashicorp.com/vault/docs/secrets/kv/kv-v2)
- [AppRole authentication](https://developer.hashicorp.com/vault/docs/auth/approle)
- [Vault Agent templates](https://developer.hashicorp.com/vault/docs/agent-and-proxy/agent/template)
- [Vault seal/unseal](https://developer.hashicorp.com/vault/docs/concepts/seal)
