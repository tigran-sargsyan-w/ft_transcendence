# Issue #115 — unified Vault-only development Compose (stages A/B)

**Status:** YAML consolidation completed; **live cutover not performed**.
Do not use `docker compose up` against the current legacy containers until the
preflight/transition phase has been explicitly reviewed and approved. Existing
Vault Raft, PostgreSQL and AppRole volumes must not be recreated.

## Target layout

The normal development configuration has exactly two Compose files:

- `compose.yml`: full Vault-only application, its agents, PostgreSQL, backend,
  frontend, collector, graph engine, Nginx, and one-shot maintenance helpers.
- `infra/vault/server/compose.yml`: separately managed Vault Server, Raft,
  backend AppRole storage, TLS and operator maintenance helpers.

The project names remain `transcendence-app-dev` and
`transcendence-vault-dev`. The existing cross-project network is
`transcendence-vault-dev_vault_private`, and the persistent volumes keep
their original Docker names. The normal development profile does not expose
PostgreSQL, Backend, Frontend, Collector, Graph Engine or Docker Socket Proxy
ports; loopback Nginx is the public development entry point.

Legacy `compose.vault.yml`, `compose.dev.yml` and tracked runtime overlays
have been consolidated and are no longer selected by any normal script.
The Clean-Room templates remain separate **test fixtures**, never a normal
runtime profile. The old per-user overrides in `$HOME/.local/state` are **not**
deleted: existing Docker labels may still refer to them until cutover.

## Configuration and secret contract

`FT_DEV_TLS_DIR` is a **non-secret path** to the existing trusted TLS
directory (default:
`${XDG_STATE_HOME:-$HOME/.local/state}/transcendence/dev/tls`).
The normal startup entry point will set this variable only for child
processes; developers will not have to export it manually.

The application receives its runtime database DSN through
`DATABASE_URL_FILE` from the restricted Vault Agent mount. No application
database URL or PostgreSQL bootstrap password belongs in Compose environment.
An **existing, initialized** PostgreSQL volume is a prerequisite to routine
startup. The default `POSTGRES_USER`/`POSTGRES_DB` values in the new model are
provisional until the live deployment identity has been checked. If those
values differ, stop and reconcile **non-secret identity settings** before
cutover. Never create another PostgreSQL volume to solve a mismatch.

Optional Compose `develop.watch` rules live in the application file; normal
`up` does not start watch mode. One-shot provisioning, migration and
maintenance services remain explicitly profile-gated.

## Read-only validation and cutover boundary

```bash
python3 -m unittest discover -s infra/vault/scripts -p 'test_compose_unification.py' -v
python3 -m unittest discover -s infra/vault/scripts -p 'test_dev_preflight.py' -v
./scripts/dev-start.sh --check
```

`--check` inspects existing containers and both target Compose models. It
accepts only the known historical labels or the exact single-file labels; it
does **not** recreate anything. If historical labels are found, it reports
that cutover remains required. Normal `./scripts/dev-start.sh` stays disabled.

The standalone application migration helpers also refuse to act on old
development Compose provenance, preventing unintentional container recreation
while the model changes. Clean-Room projects remain independently supported.

**Separate upcoming work (stages C–E):** verify live PostgreSQL identity,
validate service-by-service diffs and data preservation, explicitly cut over
containers, implement one-command Vault readiness/unseal/recovery and
migration orchestration, run isolated Clean-Room integration tests, then
revise operator onboarding. No automatic bootstrap or credential rotation is
introduced by this consolidation.

Never use `docker compose down -v`, delete a Raft/PostgreSQL volume, overwrite
the TLS CA or initialize an already prepared Vault as part of cutover.
