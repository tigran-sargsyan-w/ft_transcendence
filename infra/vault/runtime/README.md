# Issue #115 — development runtime profile (phase 1)

`./scripts/dev-start.sh --check` is **read-only**. It inspects the existing
WSL2 development installation and validates the proposed Docker Compose
model without running `up`, recreating containers or changing volumes.
Routine startup is deliberately **not implemented** in this phase.

The known local installation uses:

- Vault project: `transcendence-vault-dev` (existing Raft volume).
- Application project: `transcendence-app-dev` (existing PostgreSQL volume).
- TLS and local operator overrides: `$XDG_STATE_HOME/transcendence/dev/`, or
  `$HOME/.local/state/transcendence/dev/` when `XDG_STATE_HOME` is unset.
- Existing local `vault.override.yml` and `app.override.yml` are used **only to
  verify container provenance**, not as the proposed runtime overlays.
- The tracked files in this directory define the future normal application
  model. The Clean-Room harness stays isolated and unchanged.

The checker validates Docker labels and original Compose paths, persistent
volumes, private network, mounted TLS identity, local certificate validity,
all required service profiles, Vault Agent CA mounts, and that a final Vault
Compose model does not contain a database URL or PostgreSQL bootstrap password.
It emits only non-secret diagnostics and refuses ambiguous or missing state.

## Why we are not starting services yet

Today the running application still carries a local Clean-Room-based override
in its Compose labels. Applying the new runtime overlays directly with
`docker compose up` could recreate containers, change published ports, or
accidentally create a new volume. The checker is **not** authorization to
apply those changes. A separate reviewed cutover is required before enabling
`./scripts/dev-start.sh` without `--check`.

The non-Vault Compose workflow remains untouched. In the new Vault runtime
model the initial PostgreSQL bootstrap password is omitted because an
existing data volume must already exist. Provisioning a missing cluster
and rotation of existing PostgreSQL credentials are separate workflows.
The checker uses a deliberately empty interpolation placeholder for the old
base Compose password field and verifies that the **merged** Vault-mode
model does not retain it. It never reads or stores a password.

## Safety

- Do not run `docker compose down -v`, `docker volume rm`, or reset Vault.
- Do not point ordinary development at a Clean-Room project or TLS identity.
- If a volume, label, mount or certificate differs, investigate before any
  write operation; never auto-create a replacement resource.
- First-time trusted bootstrap, AppRole recovery, unseal and migrations retain
  their existing independent tooling until subsequent phases of #115.
