# Local DEV AppRole SecretID limit migration

This procedure applies **only** to the local ft_transcendence development Vault.
It is not a production AppRole recommendation. Do not put Vault tokens,
SecretIDs or unseal shares into Git, command arguments, shell history or logs.

## Target configuration

All three roles (`backend-reader`, `migrator-reader`, and
`postgres-provisioner-reader`) use:

- `bind_secret_id=true` (unchanged)
- `secret_id_num_uses=0` (unlimited successful logins)
- `secret_id_ttl=2160h` (90 days **for newly issued SecretIDs**)
- `token_ttl=1h`, `token_max_ttl=4h` (unchanged)
- the same exact role-specific policies, without a default policy (unchanged)

The `approle/` auth mount must also have `max_lease_ttl=2160h`.
Vault's usual `768h` mount cap would otherwise shorten a requested 90-day
SecretID lifetime. Other roles on this DEV mount could be affected by the
higher mount maximum; the three managed roles still enforce a 4-hour maximum
token TTL. Do not change the system-wide max TTL or other auth mounts.

## Existing VM: operator-approved one-time change

1. Apply and review the patch on `perf/vault-unified-startup`. Run the offline
   unit tests before updating the VM. Keep Vault unsealed and the stack intact.
2. In an **interactive** VM terminal, run:

   ```bash
   ./scripts/bootstrap.sh --prepare-vault-config
   ```

   Read the confirmation and approve this one-time role/mount configuration
   change only if the existing Vault is trusted. Routine `make up`, `make
   health`, and `--check-vault-config` **never** update these settings.
3. After `RESULT: VAULT CONFIG READY`, verify without changes:

   ```bash
   ./scripts/bootstrap.sh --check-vault-config
   ./scripts/bootstrap.sh --check-backend-agent
   make health
   make check-all
   ```

Only an existing exact **legacy** `20` uses / `168h` SecretID TTL or a
partially completed migration to the target values is eligible for automatic
property-specific updates in the explicitly confirmed `apply` mode. Unknown
role settings, ACL drift, unsafe token TTL or an unexpected auth-mount TTL
cause a fail-closed error requiring operator investigation. Retrying an
interrupted migration is supported; no existing role is deleted or recreated.

## Already-issued SecretIDs

Changing role defaults **does not extend or replenish previously issued
SecretIDs**. The bootstrap intentionally does not rotate, revoke, overwrite
or print existing AppRole credentials. Existing Vault Agent tokens may remain
usable despite an expired or exhausted SecretID, until new authentication is
needed. A separate, explicit operator-triggered rotation may be required for
the backend, migrator or provisioner to receive credentials under the new
limits. Use the already-reviewed rotation workflow in
`infra/vault/scripts/rotate-approle.py`, with its validation/finalization
steps, only after checking the current state and obtaining approval. Never
try to fix these limits by reinitializing Vault or deleting Docker volumes.

This change does not diagnose or fix the intermittent 90–188 second Docker
Compose timeout observed during read-only Vault configuration checks.
