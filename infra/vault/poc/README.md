# HashiCorp Vault — Isolated POC

An isolated proof of concept for secure secret storage, machine authentication, and automated secret delivery in **ft_transcendence**.

This POC validates the integration of HashiCorp Vault, AppRole authentication, least-privilege access policies, and Vault Agent before connecting them to the main application infrastructure.

**Status:** Functional POC — manually validated, not production-ready.

**Tracking:** [Issue #92](https://github.com/tigran-sargsyan-w/ft_transcendence/issues/92) · [PR #100](https://github.com/tigran-sargsyan-w/ft_transcendence/pull/100)

## 1. Overview

The goal is to separate application secrets from application configuration and provide controlled access to those secrets.

The POC demonstrates:

- HashiCorp Vault in normal server mode with persistent Raft storage.
- TLS-encrypted communication and certificate verification.
- KV v2 secret storage, versioning, and rollback.
- AppRole authentication with limited token privileges.
- Read-only ACL policies following the principle of least privilege.
- Vault Agent automatic authentication and secret rendering.
- Private Docker volumes with restricted permissions.
- Manual SecretID provisioning, replacement, and revocation.
- Recovery after container restarts and temporary Vault unavailability.

Only synthetic test secrets are used.

No changes are made to the main NestJS backend, PostgreSQL database, or root Docker Compose stack.

## 2. Architecture

```text
                  Trusted Operator
                 Bootstrap / Unseal
                         |
                         v
                +------------------+
                |   Vault Server   |
                |------------------|
                | TLS              |
                | Raft Storage     |
                | KV v2            |
                | ACL Policies     |
                | AppRole Auth     |
                +------------------+
                         |
                  HTTPS + AppRole
                         |
                         v
                +------------------+
                |   Vault Agent    |
                |------------------|
                | Auto-auth        |
                | Token renewal    |
                | Template render  |
                +------------------+
                    |          |
                    |          v
                    |    Rendered Volume
                    |    /vault/rendered
                    |
               Auth Volume
               /vault/auth
               RoleID + SecretID
```

The Vault Agent authenticates using AppRole, reads only authorized secrets, and writes the values into protected runtime files.

Applications consuming these files do not need a Vault SDK or a Vault Root Token.

The rendered secret file is not yet connected to a real application.

## 3. Project Structure

```text
infra/vault/poc/
├── .gitignore
├── README.md
├── compose.yml
├── vault.hcl
├── agent.hcl
├── tls-init.sh
├── policies/
│   └── poc-readonly.hcl
└── tls/                   # Generated locally; Git-ignored
```

| File | Purpose |
| --- | --- |
| `compose.yml` | Docker services, networking, and volumes |
| `vault.hcl` | Vault Server configuration |
| `agent.hcl` | Vault Agent authentication and secret rendering |
| `tls-init.sh` | Local TLS certificate generation |
| `policies/poc-readonly.hcl` | Least-privilege secret access policy |
| `.gitignore` | Prevents accidental tracking of local sensitive files |

Persistent Vault data, AppRole credentials, and rendered secrets are stored in Docker named volumes rather than the Git repository.

## 4. Prerequisites

- Docker Engine with Docker Compose v2
- Bash
- OpenSSL
- Permission to run Docker commands
- `sudo` or equivalent permissions for adjusting local TLS key ownership on Linux

All commands below are executed from:

```bash
cd infra/vault/poc
```

No host installation of the Vault CLI is required. Vault commands are executed inside containers.

## 5. First-Time Setup

The initial setup is manual. Run it only when creating a new Vault instance.

### Step 1 — Generate TLS certificates

```bash
bash ./tls-init.sh
```

The script generates a local Certificate Authority and a TLS certificate for the Vault Server.

Generated files:

```text
tls/
├── ca.crt
├── vault.crt
└── vault.key
```

The CA private key is deliberately stored outside the repository:

```text
${XDG_DATA_HOME:-$HOME/.local/share}/ft-transcendence/vault-poc-ca/ca.key
```

The server certificate is valid for 90 days, while the local CA certificate is valid for 365 days.

The script refuses to overwrite existing keys.

### Step 2 — Set TLS private key permissions

The Vault container must be able to read the TLS private key.

For the pinned image and the tested Linux Docker setup:

```bash
sudo chown 100:1000 tls/vault.key
sudo chmod 0400 tls/vault.key
```

These values correspond to the Vault container user's UID and GID in the tested environment. Other Docker user-namespace configurations may require different ownership handling.

Verify the certificate:

```bash
openssl verify -CAfile tls/ca.crt tls/vault.crt
```

Expected:

```text
tls/vault.crt: OK
```

### Step 3 — Start Vault Server

Validate Docker Compose:

```bash
docker compose config --quiet
```

Start the Vault Server:

```bash
docker compose up -d vault
```

The `vault-storage-permissions` helper initializes the required volume directory permissions.

Check Vault status:

```bash
docker compose exec vault vault status
```

A newly created Vault instance will initially be uninitialized and sealed. Vault CLI may return a nonzero exit code while sealed.

### Step 4 — Initialize Vault

**Run this only once for a new Vault data volume.**

```bash
docker compose exec vault \
  vault operator init -key-shares=3 -key-threshold=2
```

This creates:

- Three Unseal Key shares.
- An unseal threshold of two shares.
- An Initial Root Token.

Store all recovery material securely outside the repository.

**Never commit, publish, screenshot, or share real Unseal Keys or Root Tokens.**

Losing the required unseal material can make an initialized Vault instance unrecoverable.

### Step 5 — Unseal Vault

Provide two different Unseal Key shares:

```bash
docker compose exec vault vault operator unseal
docker compose exec vault vault operator unseal
```

Enter each key at the hidden prompt.

Verify:

```bash
docker compose exec vault vault status
```

Expected:

```text
Initialized    true
Sealed         false
Storage Type   raft
```

Vault uses persistent Raft storage, so data survives ordinary container restarts. However, the current Shamir seal configuration requires manual unsealing after a Vault Server restart.

## 6. Configure KV v2, ACL, and AppRole

### Step 1 — Copy the policy into the Vault container

```bash
docker compose cp \
  policies/poc-readonly.hcl \
  vault:/tmp/poc-readonly.hcl
```

### Step 2 — Open an administrative Vault session

```bash
docker compose exec vault sh
```

Inside the container, create a temporary Vault CLI home directory:

```sh
AUTH_HOME="$(mktemp -d /tmp/vault-bootstrap.XXXXXX)" || exit 1
export HOME="$AUTH_HOME"
trap 'rm -rf "$AUTH_HOME"' EXIT

vault login -no-print
```

Enter the Initial Root Token at the hidden prompt.

The temporary token-helper directory is removed when the shell exits.

### Step 3 — Enable KV v2

```sh
vault secrets enable -path=kv -version=2 kv
```

This operation is required only once.

### Step 4 — Create a synthetic secret

```sh
vault kv put -mount=kv \
  ft-transcendence/dev/poc-demo \
  demo_value="vault-poc-test-only"
```

Read it:

```sh
vault kv get -mount=kv \
  ft-transcendence/dev/poc-demo
```

KV v2 supports version history and rollback.

### Step 5 — Register the ACL policy

```sh
vault policy write \
  poc-readonly \
  /tmp/poc-readonly.hcl
```

The policy grants read access to exactly one secret:

```hcl
path "kv/data/ft-transcendence/dev/poc-demo" {
  capabilities = ["read"]
}
```

It does not grant write, delete, list, or administrative permissions.

### Step 6 — Configure AppRole

Enable the authentication method:

```sh
vault auth enable approle
```

Create the machine role:

```sh
vault write auth/approle/role/poc-reader \
  token_policies="poc-readonly" \
  token_no_default_policy=true \
  bind_secret_id=true \
  secret_id_ttl=168h \
  secret_id_num_uses=20 \
  token_ttl=1h \
  token_max_ttl=4h
```

The role requires both RoleID and SecretID.

| Setting | Value |
| --- | --- |
| Policy | `poc-readonly` |
| SecretID lifetime | 7 days |
| SecretID usage limit | 20 successful logins |
| Token TTL | 1 hour |
| Maximum token TTL | 4 hours |
| Default policy | Disabled |

Verify:

```sh
vault read auth/approle/role/poc-reader
```

Exit the administrative session:

```sh
exit
```

Do not re-enable KV v2 or AppRole on subsequent restarts. Their configuration persists in Vault storage.

## 7. Provision Vault Agent Credentials

The dedicated provisioning service generates RoleID and SecretID and writes them directly to the private `vault_agent_auth` Docker volume.

Prepare directory permissions:

```bash
docker compose run --rm vault-storage-permissions
```

Run provisioning:

```bash
docker compose run --rm --no-deps vault-approle-provision
```

Provide the Root Token at the hidden prompt.

Expected:

```text
AppRole credentials provisioned successfully
```

The provisioning service is assigned to the `setup` profile and is not part of the normal application startup.

Credentials are stored as:

```text
/vault/auth/role_id
/vault/auth/secret_id
```

Both files are restricted to mode `0400` and the Vault container user.

The Agent mounts the authentication volume as read-only.

**Security note:** provisioning currently requires an administrative Root Token. This is acceptable only for the isolated POC and must be replaced by a narrowly scoped provisioning identity before real deployment.

## 8. Start Vault Agent

```bash
docker compose up -d vault-agent
```

Check the containers:

```bash
docker compose ps
```

Inspect Agent logs:

```bash
docker compose logs --tail=40 vault-agent
```

Expected events include:

```text
agent.auth.handler: authentication successful
agent: (runner) rendered "(dynamic)" => "/vault/rendered/demo_value"
```

The Agent authenticates through AppRole, obtains a limited token, and renders the test secret to:

```text
/vault/rendered/demo_value
```

The Agent does not receive the Root Token or the Vault Server TLS private key.

## 9. Verify Secret Delivery

Run:

```bash
docker compose exec vault-agent sh -c '
  if [ -s /vault/rendered/demo_value ] &&
     [ "$(cat /vault/rendered/demo_value)" = "vault-poc-test-only" ]; then
    echo "Secret delivery: SUCCESS"
    stat -c "Permissions: %a %u:%g" /vault/rendered/demo_value
  else
    echo "Secret delivery: FAILED"
  fi
'
```

For a fresh setup, expected output:

```text
Secret delivery: SUCCESS
Permissions: 400 100:1000
```

If the test secret has subsequently been updated, adjust the expected synthetic value.

The rendered file is held in a dedicated Docker volume and has owner-only read permissions.

## 10. Manual Validation Results

The following scenarios were successfully tested during POC development:

| Test | Result |
| --- | --- |
| Vault initialization and unseal | Passed |
| Raft persistence after restart | Passed |
| KV v2 secret creation and reading | Passed |
| KV v2 versioning and rollback | Passed |
| AppRole authentication | Passed |
| ACL denial of unauthorized writes | Passed |
| ACL denial of unauthorized secret reads | Passed |
| Vault Agent secret rendering | Passed |
| Rendered file permissions | Passed |
| Automatic secret refresh | Passed |
| Vault Agent restart and reauthentication | Passed |
| Recovery after temporary Vault unavailability | Passed |
| Regeneration of a deleted rendered file | Passed |
| SecretID replacement and old SecretID revocation | Passed |

These tests were performed manually. They are **not yet automated in CI**.

A successful Docker Compose configuration check does not replace runtime security and integration testing.

## 11. Credential Lifecycle

The POC supports manual SecretID replacement:

```bash
docker compose run --rm --no-deps vault-approle-provision
docker compose restart vault-agent
```

After provisioning, verify that the Agent successfully authenticates with the new SecretID.

Previous SecretIDs are **not automatically revoked**.

An operator must identify the current SecretID accessor and explicitly revoke old credentials through Vault's AppRole accessor endpoints.

Do not revoke the currently used SecretID before verifying replacement credentials.

Additionally:

- SecretID expiration or usage-limit exhaustion can prevent future authentication.
- Existing issued tokens are not automatically revoked when their SecretID is revoked.
- Credentials must be rotated before their configured expiration.
- Automatic provisioning and SecretID rotation are deferred.

## 12. Restart and Recovery

An ordinary Agent restart requires no manual authentication:

```bash
docker compose restart vault-agent
```

A Vault Server restart is different:

```bash
docker compose restart vault
```

With the current seal configuration, Vault becomes sealed and must be manually unsealed again:

```bash
docker compose exec vault vault operator unseal
docker compose exec vault vault operator unseal
```

Then confirm recovery through Vault status and Agent logs.

**Important:** a previously rendered secret may remain in its persistent Docker volume even while Vault is unavailable. File existence alone is not proof of current Vault availability or successful reauthentication.

For temporary shutdown:

```bash
docker compose down
```

Named Docker volumes remain available after an ordinary `down`.

**Do not use `docker compose down -v` unless intentionally destroying the POC data and credentials.**

## 13. Security Boundaries

Implemented safeguards:

- No publicly published Vault API port.
- Internal Docker network.
- TLS verification between Vault and Agent.
- Persistent Raft storage isolated from Git.
- Exact-path, read-only Vault policy.
- Short-lived and renewable AppRole-issued tokens.
- Agent running as a non-root user.
- Dropped Linux capabilities and `no-new-privileges`.
- Read-only credentials and certificate mounts.
- Restricted permissions on rendered secret files.
- No real application secrets in the POC.

### Known limitations

This is **not a production-ready Vault deployment**.

Current limitations include:

- Single-node Vault without high availability.
- Manual initialization and unsealing.
- Root Token required by the POC provisioning service.
- SecretID stored persistently in a Docker volume.
- Manual SecretID rotation and revocation.
- No response-wrapped SecretID delivery.
- Persistent rendered secrets rather than memory-backed storage.
- No automated Vault integration tests in CI.
- No automatic application credential reload.
- No production backup, disaster recovery, or monitoring setup.

The disposable POC credentials and bootstrap material must never be reused in a real environment.

## 14. Future Integration

The next phase will integrate Vault with the main `ft_transcendence` infrastructure.

Planned work:

1. Agree on the secret delivery contract with the backend maintainer.
2. Introduce a dedicated backend AppRole and ACL policy.
3. Provision separate PostgreSQL application and bootstrap credentials.
4. Render the backend database connection URL to a private runtime file.
5. Make the backend and Prisma migrations consume that file safely.
6. Add explicit startup readiness and failure handling.
7. Test application restarts, missing secrets, sealed Vault, and credential updates.
8. Strengthen provisioning and automate credential lifecycle management.

The currently proposed backend secret path is:

```text
kv/ft-transcendence/dev/backend
```

The exact runtime interface must be agreed upon before changing NestJS or Prisma startup behavior.

## 15. Documentation

- [Vault Architecture Decision — ADR 0003](../../../docs/adr/0003-vault-secret-delivery.md)
- [Vault Secret Delivery Contract](../../../docs/architecture/vault-secret-delivery.md)
- [Vault Implementation Issue #92](https://github.com/tigran-sargsyan-w/ft_transcendence/issues/92)

Official references:

- [Vault Integrated Storage (Raft)](https://developer.hashicorp.com/vault/docs/configuration/storage/raft)
- [Vault KV v2](https://developer.hashicorp.com/vault/docs/secrets/kv/kv-v2)
- [Vault AppRole Authentication](https://developer.hashicorp.com/vault/docs/auth/approle)
- [Vault Agent Templates](https://developer.hashicorp.com/vault/docs/agent-and-proxy/agent/template)

---

**Scope:** This POC demonstrates secure secret delivery and access control in an isolated environment. Integration with NestJS, Prisma, and PostgreSQL remains a separate implementation phase.