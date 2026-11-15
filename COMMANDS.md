# Developer commands & onboarding

> **ft_transcendence — team command reference**  
> Run repository commands from the **repository root**, unless noted otherwise. The guarded Vault-backed workflow in Issue #115 targets **Ubuntu 26.04 (amd64)** development hosts.

**First time with the new Vault workflow?** Read the short [team quick start](docs/dev-team-quickstart.md). For the full first-time setup and security contract, see [developer onboarding](docs/dev-getting-started.md).

## Quick command reference

| Command | What it does | Changes the environment? |
| --- | --- | --- |
| `./scripts/install-deps.sh --check` | Verify development prerequisites | No |
| `./scripts/install-deps.sh` | Install prerequisites (interactive) | Yes: host packages/configuration |
| `make initialize` | **First-time only** trusted bootstrap of a fresh VM | **Yes**: host, Vault, database, app; confirmations required |
| `make up` / `make start` | Start/check **existing** trusted containers | Starts stopped containers only |
| `make recover` | Resume existing sealed Vault using **manual** unseal prompts | Unseals Vault; resumes existing containers |
| `make health` | Read-only health report, with failing dependency | No |
| `make check-all` | Read-only application readiness | No |
| `make plan` | Read-only inventory and pending setup | No |
| `make check` | Check **host-side** hostname/Nginx TLS/CA only | No |
| `make setup` | Configure host-side hostname/Nginx TLS/CA only | Yes: host; **not** a Vault initializer |
| `make acceptance` | Three guarded, non-destructive startup/idempotency runs | May start existing stopped containers; use only on an already healthy VM |
| `./scripts/bootstrap.sh --help` | Show guarded per-stage checks and maintenance | No |

**Important:** Unlike the old documentation, **`make up` is now the recommended daily command**. It does **not** run unrestricted `docker compose up -d --build`. The former `./scripts/dev-start.sh` and raw Compose procedures are not the Issue #115 daily entry point.

## A. Clone / update

```bash
# Fresh checkout (then follow first-time setup below)
git clone https://github.com/tigran-sargsyan-w/ft_transcendence.git
cd ft_transcendence

# Existing checkout: update the intended team branch
git status -sb
git fetch origin
# Integrate/rebase only according to the team's Git workflow.
```

While Issue #115 is under review, its unified bootstrap lives on `feat/vault-unified-startup`; after the PR merges, follow the reviewed `main` branch. A Git update by itself never upgrades running Docker images or applies migrations. **Do not move an initialized project's directory casually:** Docker Compose provenance checks include absolute configuration paths.

## B. First-time setup — NEW Ubuntu VM only

```bash
./scripts/install-deps.sh --check
./scripts/install-deps.sh         # If packages/configuration are missing
make initialize                   # Interactive operator-approved first setup
make health
make check-all
```

The complete initial flow creates/validates Vault Raft with Shamir 2-of-3, TLS, AppRoles, PostgreSQL, restricted roles and Prisma migrations, and the application. It stores initial recovery material under `~/.local/state/transcendence/dev/private/` and requires secure **off-VM** custody of the necessary shares. The combined first-install path still needs independent acceptance on a separate clean VM: stop on unexpected errors and contact the infrastructure owner.

**Existing legacy Docker installation?** Do not run `make initialize` or raw Compose to “migrate” it. Use an operator-reviewed cutover, or a separate clean VM; `git pull` does not change existing Docker identities, volumes or images.

## C. Daily startup and reboot recovery

```bash
make up                  # VM has already been initialized AND Vault is unsealed
make health              # Full read-only health and dependency report
make check-all           # Full read-only readiness
```

If Vault is sealed after a VM reboot:

```bash
make recover             # Interactive: two DIFFERENT shares at hidden Vault prompts
make health
```

**Never** paste keys into shell command arguments, environment variables, tickets or chat; do not attempt `vault operator init` on an initialized volume. Recovery never silently recreates missing Docker resources or runs migrations.

Open the UI: **https://transcendence.test:8443** (on the configured VM/browser). Backend health: **https://transcendence.test:8443/api/v1/health**. For a local terminal-only check:

```bash
curl -k -I https://127.0.0.1:8443
```

`-k` disables TLS verification and is only a local diagnostic fallback; it is not proof that the application's login/WebSocket flow works. The localhost gateway is bound to the VM loopback interface.

## D. After pulling changes / migrations

**`make up` intentionally does not:** rebuild existing backend/frontend/Graph Engine images, recreate containers, apply new Prisma migrations or rotate credentials. If the incoming code changes the database schema, coordinate with Backend/DevOps. A green health result can still describe a previously built image.

A **reviewed, operator-approved** Prisma migration is performed separately, after a verified database backup:

```bash
python3 scripts/dev/bootstrap_migrations.py plan  # Read-only: detect pending migrations
./scripts/bootstrap.sh --prepare-migrations     # Changes DB schema/grants; asks y/N
./scripts/bootstrap.sh --check-migrations        # Read-only verification
```

The migration workflow uses the Vault-backed migrator and reconciles restricted database grants. It refuses unexpected checksums/history. **Do not auto-retry a failed migration.** Image upgrades for already running containers also require a separately reviewed maintenance procedure; `--prepare-backend` is not an automatic in-place upgrade mechanism.

## E. Inspect and troubleshoot safely

```bash
make plan
make health
./scripts/bootstrap.sh --check-vault
./scripts/bootstrap.sh --check-migrations
./scripts/bootstrap.sh --check-backend
./scripts/bootstrap.sh --check-nginx

docker ps -a --filter 'label=com.docker.compose.project=transcendence-app-dev'
docker ps -a --filter 'label=com.docker.compose.project=transcendence-vault-dev'
docker inspect --format '{{.State.Health.Status}}' transcendence-app-dev-backend-1
```

If `make health` reports a failing stage, run its corresponding `--check-*` command first. Inspect logs locally and redact sensitive data; never print or share Vault tokens, Shamir shares, AppRole SecretIDs, rendered PostgreSQL DSNs or private key files.

## F. Development & tests

Commands inside a package use that package's directory:

```bash
# Frontend (React/Vite)
(cd frontend && npm ci && npm run lint && npm run build)

# Backend (NestJS/Prisma)
(cd backend && npm ci && npm run lint && npm run test && npm run build)

# Collector
(cd services/collector && npm ci && npm run typecheck && npm run test:cov && npm run build)

# Graph Engine
(cd services/graph-engine && python3 -m pytest -q)

# Startup offline regressions
python3 -m unittest discover -s scripts/dev -p 'test_*.py' -q
python3 -m unittest discover -s tests/benchmarks -p 'test_*.py' -q

# Additional Vault infrastructure offline regressions
python3 -m unittest discover -s infra/vault/scripts -p 'test_*.py' -q
```

Docker builds have their own dependencies. Local Node/Python tests may require package installation or an isolated Python environment. Backend E2E tests have additional runtime prerequisites; consult their test setup before running them.

## G. Git collaboration

```bash
git status -sb
git fetch origin
git diff --check
git log --oneline -5
```

Work on feature branches and follow the team's PR/review/merge rules. Coordinate schema changes and container maintenance; do not force-push shared branches.

## H. Commands not to use for routine recovery

| Command | Reason |
| --- | --- |
| `make initialize` on an existing instance | First-install only, not reboot recovery |
| `make clean`, `docker compose down` | Can remove trusted containers/networks and break safe resume |
| `docker compose down -v`, `docker volume rm` | May delete persistent Vault/PostgreSQL data |
| Raw `docker compose up` or `--force-recreate` | Bypasses guarded container identity/startup contract |
| `vault operator init` | Only for a genuinely new, empty Raft volume |
| Ad-hoc `prisma migrate resolve` / credential rotation | Security-sensitive repair requiring explicit review |

## I. Detailed operator documentation

- [Team quick start](docs/dev-team-quickstart.md) — short instructions for teammates.
- [Developer onboarding](docs/dev-getting-started.md) — first-time initialization and daily operation.
- [Reboot recovery](docs/dev-reboot-recovery.md) — manual Shamir unseal and safe resume.
- [Health diagnostics](docs/dev-health-report.md) — read-only checks and failure localization.
- [Final acceptance #115](docs/dev-final-acceptance.md) — validation evidence and limitations.
- [Vault server runbook](infra/vault/server/vault-server-README.md) — detailed operations, including historical commands; prefer this guide's guarded entry points for Issue #115.
- [AppRole maintenance](infra/vault/scripts/APPROLE-ROTATION.md) — credential lifecycle for operators.

**Rule of thumb:** `make initialize` is for a trusted first install; `make up` is daily startup; `make recover` is for a sealed existing Vault; migrations and image upgrades are separate, approved maintenance tasks.
