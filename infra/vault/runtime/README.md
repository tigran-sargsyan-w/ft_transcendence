# Issue #115 — Unified Vault-backed Development Startup

## Status

Live cutover to the two-file Compose architecture is complete.

The unified startup has been validated against the existing
development installation. This is not a fresh-machine bootstrap
or a production deployment.

## Architecture

The development environment uses two Compose files:

- `compose.yml` — application services and maintenance helpers.
- `infra/vault/server/compose.yml` — Vault Server and operator tools.

Existing Docker project names are preserved:

- Application: `transcendence-app-dev`
- Vault: `transcendence-vault-dev`

The existing Vault Raft, PostgreSQL and AppRole volumes are reused.
Routine startup must not recreate or replace these resources.

## Prerequisites

The development machine must already have:

- Docker Engine and Docker Compose v2.
- Python 3, Bash, flock and OpenSSL.
- The existing initialized Vault Raft storage.
- The existing PostgreSQL data volume.
- Configured Vault AppRoles, ACL policies and KV secrets.
- Valid local TLS material and existing Docker networks.
- The original Shamir unseal shares stored securely outside Git.

The default TLS state directory is:

`${XDG_STATE_HOME:-$HOME/.local/state}/transcendence/dev/tls`

Fresh bootstrap, missing volumes and initial credential provisioning
are separate trusted operator procedures.

## Routine startup

From the repository root:

```bash
./scripts/dev-start.sh
```

This is the normal entry point for an already prepared installation.

The workflow:

1. Acquires the shared migration/provisioning lock.
2. Validates the existing infrastructure and Compose provenance.
3. Starts only containers that are stopped.
4. Verifies Vault initialization, seal state and Raft storage.
5. Waits for PostgreSQL and required service health.
6. Verifies Prisma migration history and checksums.
7. Refuses pending migrations in normal startup mode.
8. Verifies Vault Agent secret delivery.
9. Waits for Vite and Nginx HTTPS readiness.
10. Checks Backend HTTPS and Collector Docker Proxy access.

Running the same command again must preserve running container
identities and avoid unnecessary migrations.

No automatic Vault initialization, AppRole rotation or
PostgreSQL password rotation is performed.

## Available commands

### Read-only configuration preflight

```bash
./scripts/dev-start.sh --check
```

Validates container identity, Compose configuration, TLS,
network isolation, mounts and persistent volumes.

Preflight may recognize historical Compose provenance for
diagnostic purposes. Normal startup rejects legacy containers.

This command does not establish runtime service readiness.

### Read-only startup plan

```bash
./scripts/dev-start.sh --plan
```

Reports container states, health, Vault seal state and Prisma
migration status.

Exits nonzero when startup is required, a mandatory component
is unhealthy, Vault is sealed or migrations are pending.

It does not start containers or apply migrations.

### Normal startup

```bash
./scripts/dev-start.sh
```

Starts stopped containers without recreating them.

Pending Prisma migrations require explicit authorization.

### Controlled migrations

```bash
./scripts/dev-start.sh --migrate
```

When Prisma migrations are current, this is a no-op.

When migrations are pending, the workflow stops Backend before
running the existing controlled migration and provisioning
scripts, then audits the migration state before resuming Backend.

Migration failure leaves Backend stopped.

Do not run this mode merely to repair Vault Agent authentication.

## Vault credentials

The Backend receives PostgreSQL credentials through a
restricted Vault Agent-rendered file.

Backend AppRole parameters:

- Token TTL: 1 hour.
- Maximum token TTL: 4 hours.
- SecretID TTL: 168 hours.
- SecretID maximum uses: 20.
- Default token policy: disabled.

The Backend ACL permits its specific KV read and token
self-lookup/self-renewal operations.

Expired or exhausted SecretIDs require operator-controlled
recovery. Credential rotation is not part of routine startup.

Follow `infra/vault/scripts/APPROLE-ROTATION.md` for the
rotate/recover, Agent verification and finalize procedure.

Never print, commit or paste Vault tokens, SecretIDs,
database connection strings or Shamir shares.

## Failure handling

If the Vault Agent is unhealthy, inspect its health status
and authentication failures before restarting or rotating.

If a container is stopped, normal startup resumes that
existing container. Unexpected ownership, mounts, volumes,
TLS configuration or Compose provenance fail closed.

Frontend readiness is checked through Vite HTTP and Nginx
HTTPS with bounded retries.

Do not use `docker compose down -v` against existing
development projects.

Do not recreate Vault Raft or PostgreSQL volumes to fix
a startup error.

## Validation performed

Verified against the existing development installation:

- Nine-container unified Compose cutover.
- Repeatable startup without container recreation.
- Migration no-op when Prisma history is current.
- Detection of unhealthy Vault Agent.
- Operator-controlled Backend AppRole recovery.
- Python-to-Bash migration lock inheritance.
- Rejection of competing lock acquisition.
- Detection and recovery of a stopped Frontend.
- Vite and HTTPS readiness after container restart.
- Preservation of the other eight container identities.

Unit tests cover startup, preflight, migration auditing,
migration authorization and HTTP readiness.

## Remaining validation

A real pending Prisma migration still requires an isolated
Clean-Room integration test, including failure and rollback
behavior.

The interactive Shamir unseal path also requires an independent
end-to-end validation before being considered fully tested.

This implementation does not introduce a new auto-unseal trust
provider, automatic AppRole rotation or production high
availability.

## Developer onboarding

For an already prepared development machine:

```bash
./scripts/dev-start.sh --check
./scripts/dev-start.sh --plan
./scripts/dev-start.sh
```

Use `--migrate` only when a reviewed migration needs to be applied.

If trusted infrastructure is missing, stop and follow the
separate bootstrap or recovery procedures.
