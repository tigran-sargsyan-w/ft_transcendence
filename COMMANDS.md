# Developer Commands & Onboarding

> **ft_transcendence — team command reference**  
> Platform: Linux or WSL2, Docker Engine + Docker Compose v2.  
> Run repository commands from the **repository root**, unless otherwise stated.

This file is the team's **single entry point for commands**: checkout, local browser setup, normal startup, migrations, development checks, and troubleshooting. Detailed security and infrastructure procedures remain in their dedicated runbooks.

> [!IMPORTANT]
> **There are two different starting points.** The current `./scripts/dev-start.sh` starts an **already provisioned development installation**. It is **not** a fresh-machine installer. A new clone on a machine with no Vault, PostgreSQL data, AppRole credentials, or Docker networks **cannot yet be brought up using one command**. Follow the first-time provisioning handoff in [Section 3](#3-first-time-infrastructure-provisioning-operator-only) before continuing.

## Quick command reference

| Command | What it does | Changes running infrastructure? |
| --- | --- | --- |
| `git clone https://github.com/tigran-sargsyan-w/ft_transcendence.git` | Download the repository | No |
| `make setup` | Set up local `.test` hostname, Nginx TLS certificate and CA trust (may require OS permissions) | Changes **host** configuration; not Docker |
| `make check` | Check local hostname, Nginx certificate and CA trust | No |
| `./scripts/dev-start.sh --check` | Validate existing Docker/TLS/volume/network/Compose configuration | No |
| `./scripts/dev-start.sh --plan` | Report runtime readiness, pending migrations, Vault state, Vite and HTTPS health | No |
| `./scripts/dev-start.sh` | Resume stopped containers of the existing stack and verify readiness | **Yes**: starts stopped containers; may request Shamir shares |
| `./scripts/dev-start.sh --migrate` | Explicitly authorize controlled pending Prisma migrations before Backend startup | **Yes**: can stop Backend and modify PostgreSQL schema |
| `docker ps --filter 'label=com.docker.compose.project=transcendence-app-dev'` | List application containers | No |
| `docker exec transcendence-vault-dev-vault-1 vault status` | Inspect Vault initialization/seal status | No |
| `python3 -m unittest discover -s infra/vault/scripts -p 'test_*.py' -q` | Run infrastructure Python unit tests | No live Docker changes expected; tests use mocks |

**Do not use `make up` as the routine startup command.** The current `Makefile` still runs `docker compose up -d --build` directly; it does not enforce the unified startup safety checks. Also avoid `make clean`, `docker compose down`, `--force-recreate`, and `down -v` on an existing environment unless an operator has reviewed the consequences.

## 1. Clone and select your branch

```bash
git clone https://github.com/tigran-sargsyan-w/ft_transcendence.git
cd ft_transcendence
git status -sb
```

Normally work from `main` once the unified startup changes have been merged. **Until Issue #115 is merged**, `scripts/dev-start.sh` is available on the feature branch:

```bash
git fetch origin
git switch --track origin/feat/vault-unified-startup
```

The feature branch may differ from `main`. Coordinate with the team before changing branches on a prepared Docker host or applying new migrations.

**Repository location matters:** the existing-container preflight validates the absolute Compose file paths recorded in Docker labels. Cloning the same repository into a different directory does **not** automatically adopt an already-running stack.

## 2. Install prerequisites and configure browser access

### Tools

- Git, Bash, Python 3, OpenSSL and `flock` (`util-linux`).
- Docker Engine with the Docker Compose v2 plugin, and permission to access the Docker daemon.
- `mkcert` for the repository's browser-facing Nginx certificate setup.
- Node.js **22** and npm for local JavaScript/TypeScript development or tests. Docker builds manage their own dependencies.
- Linux/WSL2 environment. Under WSL2, the domain and CA-trust helpers may also update the Windows host and trust store.

Check installed tools:

```bash
git --version
docker --version
docker compose version
docker info >/dev/null
python3 --version
openssl version
command -v flock
command -v mkcert
node --version
npm --version
```

Install missing tools using your operating system's official installation instructions before proceeding. `docker info` must work for your user.

### Local domain and HTTPS certificates

The frontend is served through Nginx at **`https://transcendence.test:8443`**. The repository includes helpers for the local domain, **Nginx** certificate, and host CA trust:

```bash
make setup       # modifies local hostname/certificates/trust; may ask for privileges
make check       # verifies the local browser setup
```

These commands configure **browser-facing Nginx TLS**. They are **not** substitutes for the separate Vault TLS, initial Vault setup, or database provisioning.

> [!NOTE]
> `.env.example` documents some application settings, but copying it into `.env` **does not provision Vault or PostgreSQL**. In the unified runtime, Backend gets its database connection via a Vault-rendered **secret file**, not `DATABASE_URL`.

## 3. First-time infrastructure provisioning (operator only)

**Stop here if this is a fresh Docker host.** The current repository has no reviewed, single-command end-to-end fresh bootstrap. `dev-start.sh --check` intentionally fails when trusted containers and volumes do not already exist.

A trusted operator must establish and verify the following **once per independent environment**, in this order:

| Stage | Required result | Detailed reference |
| --- | --- | --- |
| Vault TLS | Separate Vault CA, `vault.crt` / `vault.key`, private key custody | [Vault server runbook](infra/vault/server/vault-server-README.md) |
| Docker isolation | Correct project names, private Vault network, app/default and Collector control networks, owned volumes | [Unified runtime contract](infra/vault/runtime/README.md) |
| Vault Raft | Existing or newly initialized **separate** Raft volume; Shamir **2-of-3** shares securely held outside Git | [Vault server runbook](infra/vault/server/vault-server-README.md) |
| Vault authorization | KV v2 mount, ACL policies, three AppRoles, initial SecretIDs | [Vault server runbook](infra/vault/server/vault-server-README.md) and [AppRole runbook](infra/vault/scripts/APPROLE-ROTATION.md) |
| PostgreSQL | Distinct application and migrator credentials stored in Vault and matched with PostgreSQL roles | [Vault server runbook](infra/vault/server/vault-server-README.md) |
| First migration | Reviewed Prisma migrations applied, restricted grants reconciled | [Unified runtime contract](infra/vault/runtime/README.md) |
| Application | Existing nine containers created with approved unified Compose provenance, correct TLS and volumes | [Unified runtime contract](infra/vault/runtime/README.md) |

Some commands in the long-form Vault server runbook are explicitly marked **historical**. Do not copy its old `compose up`, `start-backend.sh`, or repeated Compose-file examples onto an already prepared stack. First-time provisioning requires a reviewed procedure that matches the current two-file architecture.

**This is the remaining onboarding gap:** to make a fresh `git clone` → `make setup` → `./scripts/dev-start.sh` self-service on a new machine, the project needs a separately implemented and tested **first-time bootstrap workflow**. This documentation does not pretend that workflow exists.

## 4. Daily startup on an already provisioned machine

Run these commands from the same repository path used to provision the existing stack:

```bash
./scripts/dev-start.sh --check   # validate configuration; no changes
./scripts/dev-start.sh --plan    # inspect readiness; no changes
./scripts/dev-start.sh           # resume existing stopped containers
./scripts/dev-start.sh --plan    # confirm everything is ready
```

What `./scripts/dev-start.sh` does:

1. Acquires the shared startup/migration lock.
2. Verifies Docker identities, Compose provenance, networks, volumes and TLS.
3. Starts **existing stopped containers**, preserving already-running containers.
4. Verifies Vault Raft, Shamir 2-of-3 and the unsealed state. If sealed, requests **two original shares interactively**, with hidden input.
5. Waits for PostgreSQL and required service healthchecks.
6. Checks Prisma migration names and checksums. Normal startup **refuses pending or failed migrations**.
7. Verifies Vault Agent secret delivery and the application routes.
8. Checks Vite, Nginx HTTPS, Backend API and Docker Collector proxy readiness.

If the existing containers or trusted volumes are missing, **stop and consult the operator**. Do not try `docker compose up` to work around an identity or preflight failure.

### Open the application

After successful startup, open:

- **Web UI:** <https://transcendence.test:8443/>
- **Backend health:** <https://transcendence.test:8443/api/v1/health>

For a terminal-only check that does not require editing `/etc/hosts`, use:

```bash
curl --silent --show-error --insecure --noproxy '*' \
  --resolve transcendence.test:8443:127.0.0.1 \
  --output /dev/null --write-out 'HTTP %{http_code}\n' \
  https://transcendence.test:8443/api/v1/health
```

Expect `HTTP 200` when the service is ready. `--insecure` is only a fallback for **local development**; in a browser, `make setup`/`make check` are intended to configure certificate trust.

## 5. Database migration workflow

```bash
./scripts/dev-start.sh --plan
```

If a reviewed Prisma migration is reported as pending, coordinate with Backend/DevOps before executing:

```bash
./scripts/dev-start.sh --migrate
```

The controlled workflow stops Backend when necessary, reconciles database credentials, runs the one-shot Vault migration Agent and `prisma migrate deploy`, reapplies restricted grants, audits the resulting migration history, and resumes Backend **only if the workflow succeeds**. When no migrations are pending, this mode does not run the migration workflow.

**Never treat a failed migration as an instruction to rerun it repeatedly.** Use a reviewed recovery procedure and verify `_prisma_migrations`. Do not manually delete migration records, edit already applied migrations, or point the application to the PostgreSQL superuser.

## 6. Read-only status and diagnostics

```bash
# See both Compose projects' containers without triggering a Compose operation.
docker ps -a --filter 'label=com.docker.compose.project=transcendence-app-dev'
docker ps -a --filter 'label=com.docker.compose.project=transcendence-vault-dev'

# Inspect Vault status (exit code 2 can mean "sealed").
docker exec transcendence-vault-dev-vault-1 vault status

# Inspect a specific container health status.
docker inspect --format '{{.State.Health.Status}}' \
  transcendence-app-dev-postgres-1

# Full read-only readiness check.
./scripts/dev-start.sh --plan
```

If you need logs, start with a short tail of the **relevant** service and review locally:

```bash
docker logs --tail 50 transcendence-app-dev-backend-1
```

Logs can contain sensitive operational data. **Never paste tokens, SecretIDs, passwords, rendered DSNs, recovery shares, or unredacted sensitive logs into issues/Discord.** Do not use `docker exec cat` on Vault-rendered secret files for troubleshooting.

## 7. Local development and tests

`npm` commands are executed **inside each package directory**, not from the repository root.

### Frontend (React + Vite)

```bash
cd frontend
npm ci
npm run lint
npm run build
cd ..
```

### Backend (NestJS + Prisma)

```bash
cd backend
npm ci
npm run lint
npm run test
npm run build
cd ..
```

The Backend also provides `npm run test:e2e`. Run it only with the required test environment configured; it may depend on database and runtime services.

### Collector

```bash
cd services/collector
npm ci
npm run typecheck
npm run test:cov
npm run build
cd ../..
```

### Graph Engine

```bash
cd services/graph-engine
python3 -m pip install -r requirements.txt
python3 -m pytest -q
cd ../..
```

Prefer a virtual environment for local Python package installation where required.

### Infrastructure unit tests

```bash
python3 -m unittest discover -s infra/vault/scripts -p 'test_*.py' -q
```

### Static Compose validation

After the Vault TLS path is provisioned:

```bash
export FT_DEV_TLS_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/transcendence/dev/tls"
docker compose -f compose.yml config --quiet
docker compose -f infra/vault/server/compose.yml config --quiet
```

These validate Compose syntax/model interpolation; they **do not** prove that the environment was provisioned correctly or that services are ready.

## 8. Git collaboration commands

```bash
git status -sb            # current branch and local changes
git fetch origin          # update remote references, without merging
git diff --check          # whitespace/error check
git log --oneline -5      # recent commits
```

For a feature branch, create a short-lived branch from an **up-to-date** `main` and open a PR when ready. Follow team review rules; do not force-push or bypass repository protections. Coordinate migrations before merging changes that introduce a new Prisma migration.

> [!WARNING]
> Updating Git files does **not** update existing Docker images or automatically apply a database migration. The routine startup intentionally avoids container recreation. A code upgrade requiring image rebuilds or Compose cutover needs a separate reviewed deployment procedure.

## 9. Commands not to use casually

| Command / action | Why it needs operator review |
| --- | --- |
| `make up` | Uses direct `docker compose up -d --build`, bypassing unified startup checks |
| `make clean` / `docker compose down` | Removes containers/networks and can invalidate the existing-container contract |
| `docker compose down -v` | Can delete persistent PostgreSQL/Vault data and authentication volumes |
| `docker compose up --force-recreate` | Recreates containers, conflicting with preservation guarantees |
| `vault operator init` | Valid **only** for a genuinely new, empty Vault Raft store |
| AppRole `rotate` / `recover` / `finalize` | Security-sensitive operator maintenance, not routine startup |
| `prisma migrate resolve` | Migration-history recovery, only after reviewing actual database effects |
| Arbitrary `docker volume rm`, `docker network rm` | May destroy or detach trusted infrastructure |

## 10. Additional runbooks

- [Unified Vault-backed startup contract](infra/vault/runtime/README.md) — authoritative behavior of `dev-start.sh` and Issue #115 limitations.
- [Vault server and controlled first-time setup](infra/vault/server/vault-server-README.md) — architecture, Vault provisioning, TLS, recovery references; includes historical commands.
- [AppRole rotation and recovery](infra/vault/scripts/APPROLE-ROTATION.md) — operator-maintained credential lifecycle.
- [Vault Clean-Room tests](infra/vault/cleanroom/README.md) — isolated test fixtures; **not** a fresh-machine bootstrap.
- [Architecture documentation](docs/architecture/README.md) — service boundaries and contracts.

**Rule of thumb:** `--check` and `--plan` are inspections; plain `dev-start.sh` resumes an existing environment; `--migrate` is an explicit database-changing operation. A first-time install is still a separate, operator-controlled process.
