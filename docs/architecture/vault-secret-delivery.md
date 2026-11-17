# Vault secret delivery architecture — local development

> **Status:** Implemented for the existing local development environment.
> Not a production-ready or fresh-machine deployment.

- Owner: Tigran (Vault, TLS, ACL and infrastructure security)
- Application integration: Nathan (NestJS and Prisma)
- Decision: [ADR 0003](../adr/0003-vault-secret-delivery.md)
- Tracking: [Issue #92](https://github.com/tigran-sargsyan-w/ft_transcendence/issues/92)
- Hardening: [Issue #103](https://github.com/tigran-sargsyan-w/ft_transcendence/issues/103)

## Architecture overview

The application uses HashiCorp Vault to deliver PostgreSQL
credentials through dedicated Vault Agents.

~~~text
                 Trusted operator
             Bootstrap / Provisioning
                        |
                        v
                 Vault Server
              TLS + KV v2 + AppRole
              Persistent Raft storage
                        |
                 Private network
                        |
          +-------------+-------------+
          |             |             |
          v             v             v
       Backend       Migrator      PostgreSQL
        Agent         Agent        Provisioner
          |             |             |
          v             v             v
       Backend DSN   Migration DSN   Temporary DSNs
          |             |             |
          v             v             v
        NestJS        Prisma        PostgreSQL
                      Migrate       provisioning
~~~

Application services do not directly authenticate to Vault.
Dedicated Vault Agents use restricted AppRoles and deliver
credentials through private runtime files.

Two Compose configurations are used:

- `infra/vault/server/compose.yml` — Vault and operator tools.
- `compose.yml` — application, agents and maintenance services.

Docker project names:

- `transcendence-vault-dev`
- `transcendence-app-dev`

## Vault server and transport

Vault runs in normal server mode using:

- HashiCorp Vault 2.2.1.
- Persistent single-node Integrated Storage (Raft).
- Verified TLS with a trusted CA.
- Private Docker network `transcendence-vault-dev_vault_private`.
- No Vault API port published on the host.
- Shamir 2-of-3 initialization and manual unseal.

Persistent Raft storage does not provide automatic unseal,
high availability or protected external backups.

## KV v2 secrets

The KV v2 secrets engine is mounted at `kv/`.

| Logical secret path | Secret key | Consumers |
| --- | --- | --- |
| `kv/ft-transcendence/dev/backend` | `database_url` | Backend Agent, PostgreSQL Provisioner |
| `kv/ft-transcendence/dev/migrator` | `database_url` | Migrator Agent, PostgreSQL Provisioner |

The corresponding Vault ACL paths are:

- `kv/data/ft-transcendence/dev/backend`
- `kv/data/ft-transcendence/dev/migrator`

Database connection URLs are sensitive and must not be
printed, logged, committed or included in test evidence.

## AppRole identities and ACLs

| AppRole | Policy | KV access |
| --- | --- | --- |
| `backend-reader` | `backend-readonly` | Backend only |
| `migrator-reader` | `migrator-readonly` | Migrator only |
| `postgres-provisioner-reader` | `postgres-provisioner-readonly` | Backend and migrator |

The Backend Agent also has permission for token
self-lookup and self-renewal.

Application AppRoles do not receive Vault administrative
permissions, root tokens or Shamir unseal shares.

Current bootstrap configuration:

- SecretID TTL: `2160h` (90 days).
- SecretID maximum uses: `0` (unlimited).
- Token TTL: `1h`.
- Token maximum TTL: `4h`.
- Default token policy: disabled.
- SecretID binding: required.

These are the configured bootstrap targets. Previously
issued credentials may retain earlier restrictions.
Live AppRole settings require separate verification.

SecretID rotation and recovery are operator-controlled.

## Backend credential delivery

Configuration:

`infra/vault/agent/backend.hcl`

The persistent Backend Agent reads the backend KV path
and renders the database connection URL to:

`/vault/rendered/database_url`

The rendered file uses permissions `0440`.

The Backend mounts the same dedicated secret volume
read-only at:

`/run/secrets/backend/database_url`

The Backend receives its file location through:

`DATABASE_URL_FILE=/run/secrets/backend/database_url`

Backend runtime identity:

- UID: `10001`
- GID: `20000`

The Backend container has:

- Read-only root filesystem.
- All Linux capabilities dropped.
- `no-new-privileges`.
- No AppRole authentication volume.
- No Vault administrative credentials.

The application may load the connection URL into process
memory or its runtime environment. File delivery does not
guarantee that credentials never exist in process memory.

## Prisma migration credential delivery

Configuration:

`infra/vault/agent/migrator.hcl`

The one-shot Migrator Agent renders its dedicated
database URL to:

`/vault/rendered/database_url`

The `backend-migrate` container receives the file at:

`/run/secrets/migrator/database_url`

Migrator runtime identity:

- UID: `10011`
- GID: `20010`

The migration service executes `prisma migrate deploy`.

The workflow is coordinated by:

`infra/vault/scripts/run-migrations.sh`

It includes:

1. PostgreSQL readiness.
2. Controlled credential provisioning.
3. Fresh migration secret delivery.
4. Prisma migration execution.
5. PostgreSQL grant reconciliation.
6. Temporary migration secret cleanup.

Migration cleanup is attempted on normal exit and
handled interruptions. Cleanup and failure scenarios
require reproducible validation.

## PostgreSQL provisioning

Configuration:

`infra/vault/agent/postgres-provisioner.hcl`

The dedicated Provisioner Agent temporarily renders:

- `/vault/rendered/backend_database_url`
- `/vault/rendered/migrator_database_url`

The provisioning workflow uses separate application
and migration identities.

The PostgreSQL application role must not receive
superuser privileges, role creation permissions
or schema creation privileges.

Provisioning secrets are removed by the dedicated
cleanup workflow.

## Application startup

For an already provisioned development installation:

~~~sh
./scripts/dev-start.sh
~~~

Read-only verification:

~~~sh
./scripts/dev-start.sh --check
./scripts/dev-start.sh --plan
~~~

Explicit migration authorization:

~~~sh
./scripts/dev-start.sh --migrate
~~~

Normal startup validates:

- Existing container and volume identities.
- Vault initialization and unseal state.
- TLS trust and private networking.
- PostgreSQL and service health.
- Current Prisma migration state.
- Vault Agent secret delivery.
- HTTPS frontend and backend readiness.

Pending migrations require explicit authorization.

Normal startup does not automatically initialize
Vault, provision AppRoles or rotate credentials.

## Security boundaries

- SecretID files are private to their corresponding agents.
- Rendered secret volumes are restricted by consumer.
- Backend and migrator receive different database credentials.
- Administrative Vault tokens are not distributed to applications.
- Vault communication requires certificate verification.
- Docker host administrators can access persistent volumes.
- Sensitive values must not appear in Git, logs or evidence.
- Temporary credentials require explicit lifecycle cleanup.
- KV updates do not guarantee application hot-reloading.

## Operational limitations

This is a local development implementation.

It does not provide:

- Production high availability.
- Production-grade external backups.
- Automatic unseal through trusted external KMS/HSM.
- Automatic end-to-end PostgreSQL password rotation.
- Dynamic leased PostgreSQL credentials.
- Guaranteed automatic application credential reload.
- Fully unattended first-time provisioning.

A sealed Vault requires operator-assisted unseal.
A fresh machine requires separate trusted bootstrap.

The existing single-command workflow applies only
after these infrastructure prerequisites are satisfied.

## Implementation references

- [Original ADR](../adr/0003-vault-secret-delivery.md)
- [Runtime operations](../../infra/vault/runtime/README.md)
- [Vault server operations](../../infra/vault/server/vault-server-README.md)
- [AppRole rotation](../../infra/vault/scripts/APPROLE-ROTATION.md)
- [Vault ACL policies](../../infra/vault/policies/)
- [Vault Agent configurations](../../infra/vault/agent/)

See Issue #103 for reproducible hardening evidence.
