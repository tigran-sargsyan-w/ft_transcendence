# Vault-backed development onboarding (Issue #115 / Phase 6E)

This guide documents the **development VM** startup. Run commands at the repository root on a supported **Ubuntu 26.04 (amd64)** host as a normal user with Docker access; do not use `sudo` with bootstrap commands. Hostname, certificate and local CA trust checks are local to the VM. The HTTPS gateway listens only on `127.0.0.1:8443` and HTTP redirect on `127.0.0.1:8080`; access from another host requires a separately reviewed forwarding setup.

## Architecture and trust boundaries

- The isolated `transcendence-vault-dev` Vault server uses Raft and Shamir **2-of-3**; recovery material is written outside Git and must be backed up separately off-VM.
- `transcendence-app-dev` uses PostgreSQL 18 and Prisma, a dedicated backend Vault Agent, least-privilege application/migration roles and a distinct Vault-backed migration/provisioning workflow.
- The backend receives the database URL through a private rendered secret file, not through an application `DATABASE_URL` environment variable. Nginx exposes the local TLS gateway; PostgreSQL and Vault do not publish host ports.
- The **host-based** bootstrap orchestrator delegates to independently runnable stage helpers. There is no giant privileged orchestration container. New app containers, migrations and AppRole credentials are **not** created by routine startup.
- This VM configuration requires a trusted operator for initial setup and for Shamir unseal after a restart. It is not production-grade Auto Unseal or HA; credential rotation and a new unseal provider are separate tasks.

## First-time trusted setup: new VM only

1. Check out the project on the dedicated Ubuntu VM, including the intended reviewed commit. Keep local work and sensitive materials outside Git.
2. Install prerequisites **interactively** if needed, then verify the environment:

   ```sh
   ./scripts/install-deps.sh
   ./scripts/install-deps.sh --check
   ./scripts/bootstrap.sh --plan
   ```

3. Run the single first-time entry point **in an interactive terminal**:

   ```sh
   make initialize
   # Equivalent: ./scripts/bootstrap.sh --initialize
   ```

   The operator must review and individually approve the necessary preparation stages (host TLS/CA, Docker resources, Vault, KV/ACL/AppRoles, initial DB secrets, PostgreSQL, roles, Prisma migrations, runtime Agent, backend, support services, frontend and Nginx). The helper never silently replaces a detected existing initialized Vault or PostgreSQL cluster. **Do not use initialization to recover from a reboot or a failed check.**

4. Secure the generated Vault recovery material according to the local procedure and retain **two distinct Shamir shares in approved secure storage outside the VM**. The protected local `vault-init.json` contains root-level recovery material: never print it, commit it or paste it into an issue. Maintain an independent backup of persistent application data.
5. Validate readiness:

   ```sh
   make health
   make check-all
   ```

   Open `https://transcendence.test:8443` from the configured VM browser and verify that the actual application works. A green infrastructure check does not substitute for login, session or WebSocket functional E2E tests.

## Ordinary development day

On an initialized and **unsealed** VM, the normal one-command entry point is:

```sh
make up
# Same guarded workflow as: ./scripts/bootstrap.sh --start
```

`make up` intentionally **does not** run `docker compose up -d --build` anymore. Routine startup reuses verified containers and fails closed if a required container is missing, untrusted or persistently unhealthy. It does not reconcile new migrations automatically; an explicit, reviewed provisioning/migration workflow is required when the database/schema genuinely changes.

To inspect state without mutations:

```sh
make plan
make health
make check-all
```

`make check` checks **only** the host domain, Nginx certificate and OS CA trust (legacy helper); use `make health` for the full runtime. `make acceptance` is an **opt-in** validation that repeatedly calls safe `--start` on an already healthy VM and compares container, persistent volume, network and Prisma migration-history metadata. It is not needed on every login.

## After a VM restart: operator-assisted recovery

The Vault server container can be running but its Raft storage **sealed**. From a real interactive terminal, with two valid distinct Shamir shares available **outside the VM**:

```sh
make recover
# Equivalent: ./scripts/bootstrap.sh --recover
```

Only enter shares when the Vault CLI itself requests them with hidden input. The recovery path validates the **existing** Vault container, does not reinitialize or replace it, then resumes existing verified app containers. Finish with `make health`. If you lack shares, stop; **never reinitialize Vault**, delete its volume or leak shares to bypass the guard. Details: [VM reboot recovery](dev-reboot-recovery.md).

## Failures and safe maintenance

- `make health` reports the first observable failing component without exposing raw Docker output. Use the named `./scripts/bootstrap.sh --check-*` command for a focused, read-only check. An intermittent failure is not reported as success.
- If a container is missing, untrusted or permanently unhealthy, stop and investigate; do not run `make initialize`, raw `docker compose up`, `docker compose down -v`, `make clean`, credential rotation or migrations as an automatic fix.
- `make down` and `make clean` remain **legacy raw-Compose maintenance targets** for compatibility. They are **not** part of the trusted startup/recovery contract; in particular `make clean` removes application containers/networks and can make the guarded routine start unable to recover. Discuss any required intentional teardown with the infrastructure owner first.
- Stage-specific `--prepare-*` tools remain available for **reviewed, explicit maintenance** and may request confirmation; use `./scripts/bootstrap.sh --help` to see them. Changes to Vault policies, credentials or Prisma migrations require separate review and should not be disguised as routine startup.
- Do not log root tokens, unseal shares, SecretIDs, DSNs or entire Docker inspect records. Do not share private `vault-init.json`. Only store non-secret test results in issues/PRs.

## Further reading / acceptance

- [Read-only end-to-end health and diagnostics](dev-health-report.md)
- [Reboot recovery with manual Shamir unseal](dev-reboot-recovery.md)
- [Non-destructive startup idempotency checks](dev-startup-idempotency.md)
- [Phase 6E final acceptance checklist](dev-final-acceptance.md)

This document describes the development environment only; it is not a production deployment, automated Auto Unseal, credential rotation plan, or full functional E2E test suite.
