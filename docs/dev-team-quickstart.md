# Team quick start — Vault-backed development

**New to the Vault startup? Start here.** All commands below run from the repository root. This workflow is designed for an **Ubuntu 26.04 (amd64)** development host with Docker access as a non-root user. Other hosts need a separately verified setup.

## 1. My Vault environment is already initialized

Keep using the **same repository directory and Docker environment** as before. After updating your branch:

```bash
make up
make health
```

Open the URL printed by `python3 -m scripts.dev.configuration origin` in the browser configured on that host. The public settings are in `config/development.env`; do not create a root `.env` for this workflow.

If Vault is **sealed** (typically after a reboot), use `make recover` in an interactive terminal and enter **two different Shamir shares** into the hidden Vault CLI prompts. Then run `make health` again. Do **not** run `make initialize` to recover a sealed Vault.

## 2. This is a completely new Ubuntu VM

```bash
make initialize  # First install: prerequisites, images and guarded stages
make health      # Read-only verification
```

Git and Make must be present to obtain the repository and invoke this
command. Package installation, image downloads and initial Vault
provisioning require explicit operator approval. If Docker group access
changes, log out and back in before retrying. Do not run the initializer
with sudo.

`make initialize` sets up the local host, Vault, PostgreSQL, roles, migrations and application through an operator-approved flow. It is **not** routine startup, and its full first-install path has **not yet been independently accepted on another clean VM**. Ask the infrastructure owner if a step fails; do not retry by deleting Docker volumes.

Vault uses **Shamir 2-of-3**. Initialization stores sensitive recovery material under `~/.local/state/transcendence/dev/private/`. Securely back up the shares **outside the VM** and never share the root token or shares in Git, Discord, screenshots or logs. Each independent installation creates its **own** Vault material; a Git clone does not contain recovery keys.

## 3. I have an older, pre-#115 Docker installation

**Stop and coordinate a cutover with DevOps.** The guarded startup checks existing Docker identities, Compose provenance and persistent volumes. `git pull` alone does **not** migrate an old environment, and `make initialize` is not a safe replacement for an existing installation. A separate clean VM may be the simplest onboarding option.

## 4. After a Git update: what `make up` does NOT do

`make up` starts/checks **already created, trusted containers**. It deliberately **does not** rebuild existing images, upgrade running services, apply new Prisma migrations, rotate credentials or recreate persistent data. If your pull introduces backend/frontend/Graph Engine changes or a new migration, **coordinate the upgrade with the infrastructure owner** before expecting the running app to reflect the new code.

For a **reviewed, explicitly approved** migration (with a database backup), the operator uses `./scripts/bootstrap.sh --prepare-migrations`; it is **not** a daily startup command.

## Useful commands

| Command | Purpose |
| --- | --- |
| `make up` | Normal startup of a prepared and unsealed environment |
| `make recover` | Restore an existing sealed Vault with two operator-entered shares |
| `make health` | Read-only end-to-end health report |
| `make check-all` | Read-only readiness check |
| `make plan` | Read-only inventory and pending work |
| `./scripts/bootstrap.sh --help` | List operator-only maintenance steps |

**Never use** `docker compose down -v`, `make clean`, `vault operator init`, or arbitrary `docker compose up --force-recreate` to fix a failed startup. Stop and inspect the failing `--check-*` stage first.

**More detail:** [Team command reference](../COMMANDS.md) · [Developer onboarding](dev-getting-started.md) · [Reboot recovery](dev-reboot-recovery.md).
