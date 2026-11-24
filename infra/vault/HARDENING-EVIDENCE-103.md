# Vault Phase 3 — Hardening and Security Evidence

Tracking: [Issue #103](https://github.com/tigran-sargsyan-w/ft_transcendence/issues/103)

## Scope

Manual security verification of the existing local development
installation in November 2026.

Environment:

- HashiCorp Vault 2.2.1
- Docker Compose development deployment
- Vault KV v2 and AppRole authentication
- Vault Agent file-based secret delivery
- PostgreSQL application and migration roles
- Local HTTPS gateway

These results are observations from an existing installation.
They are not a production security certification or evidence
of a successful fresh-machine bootstrap.

## Verification results

| # | Area | Result |
| --- | --- | --- |
| 1 | Startup preflight, TLS and Compose provenance | PASS |
| 2 | Secret file permissions and consumer access | PASS |
| 3 | Backend Vault Agent read and denied operations | PASS |
| 4 | Docker network and volume isolation | PASS |
| 5 | Sensitive Docker environment variable names | PASS |
| 6 | Sensitive-pattern scan of available container logs | PASS |
| 7 | PostgreSQL role and schema privileges | PASS |
| 8 | Additional PostgreSQL table privileges | PASS |
| 9 | Temporary migration and provisioning secret cleanup | PASS |
| 10 | HTTPS certificate verification without insecure mode | PASS |

PASS means the specific tested condition matched expectations.
It does not establish security against every possible threat.

## 1. Read-only infrastructure preflight

Command:

~~~sh
./scripts/dev-start.sh --check
~~~

Observed: all ten reported preflight checks passed.

Verified areas include:

- Existing container identities and Compose provenance
- TLS chain, hostname and certificate expiration
- Persistent Raft, Agent and PostgreSQL volumes
- Private Vault and Collector networks
- Agent TLS mounts and application secret-file mounts

Result: PASS.

## 2. Secret file permissions

Observed permissions and ownership:

| File | Mode | UID:GID |
| --- | --- | --- |
| Backend Agent AppRole role_id | 0400 | 100:1000 |
| Backend Agent AppRole secret_id | 0400 | 100:1000 |
| Agent-rendered backend database URL | 0440 | 100:20000 |
| Backend Agent runtime token | 0400 | 100:20000 |
| Backend-mounted database URL | 0440 | 100:20000 |

The Backend could read its intended database secret.
The tested unintended AppRole and migrator secret paths
were not present in its container.

Result: PASS for the checked files and paths.

## 3. Vault ACL isolation

Backend Agent authentication was tested with actual
Vault requests.

Observed:

- Read of the backend KV secret: succeeded.
- Read of the migrator KV secret: failed.
- AppRole administration request: failed.
- Vault system mounts request: failed.

No secret values were printed.

The negative checks verified command failure rather than
capturing and independently classifying each HTTP error.
They do not establish that every failure was HTTP 403.

Result: PASS for the tested access boundary.

## 4. Docker isolation

Observed network placement:

| Container | Network |
| --- | --- |
| Vault Server | vault_private |
| Backend Vault Agent | vault_private |
| Backend | application default |
| Frontend | application default |
| Collector | application default + collector_control |
| Docker Socket Proxy | collector_control |

Vault Server had no application network attachment.
Backend did not mount the AppRole authentication volume.
The backend secret volume was mounted read-only.

Result: PASS for tested network and mount isolation.

Docker host administrators remain privileged and can
access sensitive Docker volumes.

## 5. Docker environment inspection

The Docker inspect audit checked selected credential-related
environment variable names for Vault Server, Backend Agent,
Backend and PostgreSQL.

Observed:

- No direct credential variables identified.
- Backend used DATABASE_URL_FILE.
- No DATABASE_URL, POSTGRES_PASSWORD, VAULT_TOKEN,
  VAULT_ROOT_TOKEN or SECRET_ID assignments identified
  in the inspected container environment metadata.

Result: PASS for the inspected names.

This was a name-based audit, not an exhaustive analysis
of every environment variable or application process.

## 6. Container log inspection

Available Docker logs from the preceding 168 hours
were scanned for sensitive patterns.

Containers:

- Vault Server
- Backend Vault Agent
- Backend
- PostgreSQL

Patterns included database connection URLs, credential
assignments, authorization headers and private key material.

Observed: no matching patterns in the inspected logs.

Result: PASS.

The scan cannot detect arbitrary or unrecognized secret
formats, and does not cover logs outside the inspected scope.

## 7. PostgreSQL least privilege

Observed attributes for both transcendence_app and
transcendence_migrator:

- LOGIN: true
- SUPERUSER: false
- CREATEDB: false
- CREATEROLE: false
- REPLICATION: false
- BYPASSRLS: false

Observed effective privileges:

| Permission | Granted |
| --- | --- |
| App: public schema USAGE | true |
| App: public schema CREATE | false |
| Migrator: public schema CREATE | true |
| App: users SELECT | true |
| App: users INSERT | true |
| App: _prisma_migrations SELECT | true |
| App: _prisma_migrations UPDATE | false |

Result: PASS.

## 8. Additional PostgreSQL restrictions

All six tested conditions returned false:

- App: incidents DELETE
- App: incident_events UPDATE
- App: incident_events DELETE
- App: users TRUNCATE
- App: users REFERENCES
- App: membership in transcendence_migrator

Result: PASS.

These checks cover the named operations, not every
possible PostgreSQL privilege or database object.

## 9. Temporary secret lifecycle

Two existing named volumes were inspected through
temporary, network-disabled, read-only containers.

Observed:

- Migrator database_url: absent.
- Provisioner backend_database_url: absent.
- Provisioner migrator_database_url: absent.

Result: PASS.

These observations establish absence at inspection time.
They do not alone prove cleanup on every possible failure,
crash or forced termination.

## 10. HTTPS certificate verification

Reproduction:

~~~sh
curl -q --silent --show-error   --max-time 10   --noproxy "*"   --resolve "transcendence.test:8443:127.0.0.1"   --output /dev/null   --write-out "HTTP %{http_code} | TLS verify: %{ssl_verify_result}\n"   https://transcendence.test:8443/api/v1/health
~~~

Observed:

~~~text
HTTP 200 | TLS verify: 0
~~~

This establishes successful HTTPS certificate verification
with the locally trusted CA for the tested endpoint.

The startup readiness implementation was updated to
remove --insecure and use curl -q.

A regression test verifies that TLS verification is not
disabled by the readiness command.

## Reproducible startup and test commands

Run from the repository root against the already
provisioned local development installation:

~~~sh
./scripts/dev-start.sh --check
./scripts/dev-start.sh --plan

python3 -m unittest   infra/vault/scripts/test_dev_start_http_readiness.py -v

git diff --check
~~~

Observed after the HTTPS fix:

- Five HTTP readiness unit tests passed.
- Startup plan reported current Prisma migrations.
- Frontend and HTTPS readiness checks passed.
- Startup plan reported VALID.

The detailed Vault ACL, PostgreSQL and cleanup checks
were also executed manually. Their observed results
are recorded above.

## Operational and security limitations

The local development deployment has the following
documented limitations:

1. Single-node Vault Raft is not production HA.
2. Manual Shamir unseal requires trusted operator input.
3. Fresh bootstrap and provisioning are not unattended.
4. SecretID TTL is configured for 90 days with unlimited
   uses in the current development bootstrap.
5. Previously issued SecretIDs may retain older limits.
6. Automatic PostgreSQL credential rotation is absent.
7. Dynamic database credential leases are not implemented.
8. Docker host administrators can access persistent volumes.
9. KV updates do not guarantee automatic application reload.
10. Manual checks do not replace automated CI or security
    testing against malicious clients and failure conditions.

## Remaining verification

Before closing Issue #103:

- Verify effective AppRole parameters in the running Vault
  without printing tokens or SecretIDs.
- Confirm architecture and operator documentation matches
  the current implementation.
- Review and execute the final relevant test suite.
- Record the final PR and commit references.

No Vault credential values, unseal shares, root tokens
or private TLS keys should be attached to the issue.
