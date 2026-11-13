# Development VM reboot recovery (Phase 6B)

This runbook applies **only** to the initialized Ubuntu development VM for
`ft_transcendence` using single-node Vault with Raft and **Shamir 2-of-3**.
It is not production Auto Unseal, HA, backup/restore or disaster recovery.
The Vault server never receives a persistent unseal-share mount.

## Security prerequisites

- Ensure two **distinct** valid Shamir shares are available to their authorized
  operator(s), ideally from secure storage **outside the VM**.
- Do not paste shares, Vault root tokens or `vault-init.json` into chat, shell
  command arguments, Git, logs, tickets or screenshots.
- Never use `--initialize`, `docker compose down -v`, `docker volume rm`,
  `vault operator init` or a replacement Vault container to recover a reboot.
- The trusted recovery workflow will **not** initialize/recreate Vault, generate
  credentials, run migrations, rotate keys or retrieve shares from disk.

## Before a controlled reboot

On the existing, working VM, record **non-secret** inventory outside the VM or
in a secure operations notebook:

```sh
./scripts/bootstrap.sh --check-all
docker inspect --format '{{.Name}} {{.Id}}' \
  transcendence-vault-dev-vault-1 transcendence-app-dev-postgres-1
docker volume inspect --format '{{.Name}}' \
  transcendence-vault-dev_vault_data \
  transcendence-app-dev_postgres_data
```

Ensure that the Vault recovery shares have been backed up securely **outside**
the VM and that a tested VM snapshot/backup is available where appropriate.
Never copy the key values into these test logs. A VM snapshot alone is not an
independent off-VM backup if stored on the same disk.

## After reboot

From a real interactive terminal on that Ubuntu VM, with Docker available to
this non-root user:

```sh
./scripts/bootstrap.sh --recover
```

The command validates host and Docker resources, then checks the **existing**
Vault container identity. If stopped, it only starts that same trusted container.
If initialized but sealed, it invokes `docker exec -it ... vault operator unseal`
**without a key argument**. Vault CLI prompts for two different keys with
hidden input. The orchestrator does not read or log the shares. It validates
unseal progress after each input. Once Vault is ready, it resumes only existing
trusted application containers in dependency order and verifies HTTPS health.

When Vault is already unsealed, `--recover` will skip prompts and may simply
report that the stack is ready. The ordinary fast path remains:

```sh
./scripts/bootstrap.sh --start
./scripts/bootstrap.sh --check-all
```

These commands do not create new Vault/AppRole/DB credentials or apply new
migrations. If a container is missing, untrusted, or running but persistently
unhealthy, recovery stops without recreating it.

## Manual fallback

If automatic orchestration is unavailable, a trusted operator can use the
existing Vault CLI prompt directly (do not append a key to the command):

```sh
docker exec -it transcendence-vault-dev-vault-1 vault operator unseal
# Enter the first share in the hidden prompt.
docker exec -it transcendence-vault-dev-vault-1 vault operator unseal
# Enter a different second share in the hidden prompt.
./scripts/bootstrap.sh --start
./scripts/bootstrap.sh --check-all
```

If Vault is sealed but the operator lacks two distinct shares, **stop**.
Do not reset Vault, reinitialize Raft, extract keys from an unsafe location,
or weaken the threshold. If the first key was accepted but the second is
unavailable, recovery can be continued later by an authorized operator.

## Acceptance evidence (no secret values)

After an authorized controlled reboot, record:

1. Vault reports initialized and unsealed with Raft and the expected seal.
2. The existing Vault and PostgreSQL container IDs match the pre-reboot IDs.
3. The same named data volumes are retained, and existing migration/grant
   checks pass without replaying or changing migration history.
4. `--check-all` and two successive `--start` invocations succeed.
5. Browser access via `https://transcendence.test:8443` works, with no backend
   errors. Confirm the site and API are healthy; do not rely on HTTP 200 alone.
6. Any failure message contains no shares, tokens or rendered DSNs.

Do **not** reboot the VM just to test until the offline tests and code review
have passed, recovery shares are available from an independent secure backup,
and all important local work has been saved.
