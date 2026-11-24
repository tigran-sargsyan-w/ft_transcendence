# Vault-backed development onboarding (Issue #115 / Phase 6E)

This guide documents the **development VM** startup. Run commands at the repository root on a supported **Ubuntu 26.04 (amd64)** host as a normal user with Docker access; do not use `sudo` with bootstrap commands. Hostname, certificate and local CA trust checks are local to the VM. The HTTPS gateway listens only on `127.0.0.1:8443` and HTTP redirect on `127.0.0.1:8080`; access from another host requires a separately reviewed forwarding setup.

## Architecture and trust boundaries

- The isolated `transcendence-vault-dev` Vault server uses Raft and Shamir **2-of-3**; recovery material is written outside Git and must be backed up separately off-VM.
- `transcendence-app-dev` uses PostgreSQL 18 and Prisma, a dedicated backend Vault Agent, least-privilege application/migration roles and a distinct Vault-backed migration/provisioning workflow.
- The backend receives the database URL through a private rendered secret file, not through an application `DATABASE_URL` environment variable. Nginx exposes the local TLS gateway; PostgreSQL and Vault do not publish host ports.
- The **host-based** bootstrap orchestrator delegates to independently runnable stage helpers. There is no giant privileged orchestration container. New app containers, migrations and AppRole credentials are **not** created by routine startup.
- This VM configuration requires a trusted operator for initial setup and for Shamir unseal after a restart. It is not production-grade Auto Unseal or HA; credential rotation and a new unseal provider are separate tasks.

The Vault Web UI is an [optional, on-demand administrative gateway](dev-vault-ui-on-demand.md). Use `make vault-ui-enable` only when needed; `make vault-ui-disable` closes its host-facing access.

## First-time trusted setup: new VM only

1. Check out the project on the dedicated Ubuntu VM, including the intended reviewed commit. Keep local work and sensitive materials outside Git.
2. Ensure the repository has been cloned and Git and GNU Make are
   available. `make initialize` installs missing OS/Docker prerequisites
   and downloads required container images without approval prompts. There is
   no mandatory separate `install-deps.sh`, `make setup` or manual
   `docker pull` step. Existing local image tags are preserved.

   If Docker group membership changes, log out and back in before
   rerunning initialization. This is an operator-approved, resumable
   first-install flow. Docker group membership grants root-equivalent access;
   initialization authorizes automatic enrollment only for the verified local daemon.

3. Run the single first-time entry point (a terminal is recommended for sudo authentication):

   ```sh
   make initialize
   # Equivalent: ./scripts/bootstrap.sh --initialize
   ```

   Invoking the command authorizes the fixed development provisioning stages; no additional approval prompts are required. This automatic path supports native Ubuntu Linux only, not Windows/WSL trust installation or a remote Docker daemon. Existing valid resources are retained and conflicting state is refused. Unknown untrusted browser CAs require explicit review. A sudo password request, if needed, is ordinary OS authentication, not a bootstrap confirmation. See [initialization progress and cancellation](dev-initialize-reliability.md#issue-160-initialization-progress-and-cancellation). **Do not use initialization to recover from a reboot or as a blind repair for an unhealthy stack.**

4. Secure the generated Vault recovery material according to the local procedure and retain **two distinct Shamir shares in approved secure storage outside the VM**. The protected local `vault-init.json` contains root-level recovery material: never print it, commit it or paste it into an issue. Maintain an independent backup of persistent application data.
5. Validate readiness:

   ```sh
   make health
   make check-all
   ```

   Open `https://transcendence.test:8443` from the configured VM browser and verify that the actual application works. A green infrastructure check does not substitute for login, session or WebSocket functional E2E tests.

### Independent fresh-VM verification (2026-11-22)

The combined `make initialize` path has been exercised successfully on a
**separate, clean Ubuntu 26.04 LTS (amd64) VirtualBox VM** with 4 vCPUs
and approximately 3.3 GiB guest RAM, at implementation commit `cd293e8`.
Initially, Docker was absent. The command installed all missing OS/Docker
prerequisites, paused for the expected Docker-group login refresh, and
completed after the operator reran `make initialize`. No separate
dependency-install, TLS setup or Docker image-pull command was needed.

Full-stack health, three non-destructive repeated startups, Prometheus/Grafana
dashboard rendering, a Demo Lab failure/alert/recovery cycle and retained
monitoring history after reboot were verified. The Vault/PostgreSQL container
IDs were preserved after operator-assisted Shamir unseal.

**Known pre-existing limitation:** the first `make recover` after reboot
stopped at an already-running but not-yet-ready Nginx; a second attempt
completed and health/readiness checks passed. Follow up separately in
[issue #155](https://github.com/tigran-sargsyan-w/ft_transcendence/issues/155).
This does not demonstrate guaranteed single-attempt recovery or safe upgrade
of a pre-existing legacy development stack. See the
[monitoring acceptance record](dev-monitoring.md#independent-clean-vm-acceptance--2026-11-22)
for detailed evidence.

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

`make check` checks **only** the host domain, Nginx certificate and OS CA trust; use `make health` for the full runtime. `make acceptance` is an **opt-in** validation that repeatedly calls safe `--start` on an already healthy VM and compares container, persistent volume, network and Prisma migration-history metadata. It is not needed on every login.

### Shared bootstrap progress (Issue #165)

`make up` / `make start`, `make recover`, `make health`, `make check-all`
and the host-only `make check` reuse the V3 inline progress renderer from
#160. Their stages describe real operations and use actual elapsed time.
The startup fast path still uses the existing transitive Nginx and
monitoring checks; it does not run extra per-service probes just to
populate the display. If necessary, the existing-container resume path
appears as a separate stage with nested checks and operations.

CACHED means an existing check established readiness without a change.
DONE means the represented operation completed; SKIPPED is an unneeded
operation, not a readiness assertion. NOT READY is a nonfatal intermediate
stage result only in routine startup/recovery when the existing optional
UI or fast-path policy permits continuing. The overall RESULT marker and
exit code remain authoritative. Health/check commands still fail closed.
The progress count measures completed stages, not the number of healthy
services.

Redirected output, CI, NO_COLOR, verbose and timing modes use plain logs.
For example:

```sh
FT_DEV_BOOTSTRAP_VERBOSE=1 make health
FT_DEV_STAGE_TIMING=1 make up
NO_COLOR=1 make check-all
```

Verbose mode includes reviewed orchestration events, retry attempts and
safe existing lifecycle messages. It does not forward raw subprocess
output. If a host-only check fails, run the named `python3 -m
scripts.dev.<module> check` helper locally for detailed diagnostics. For
runtime failures use the indicated `./scripts/bootstrap.sh --check-*`
entry point. Do not share unreviewed output or recovery material.

The renderer pauses for manual Shamir unseal; Vault CLI retains the real
controlling terminal and hidden prompts. The timer thread is presentation
only and is joined at exit. The original V3 visual limitation during
terminal resize remains; keep terminal dimensions stable. The separate
plan/acceptance and Vault UI presentation follow-ups are #166 and #167;
their behavior is not changed by #165.

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

## Operational monitoring

Prometheus and Grafana are part of the guarded startup,
readiness and recovery contract.

Use https://transcendence.test:8443/grafana/ from an
authorized browser with trusted TLS.

The monitoring guide documents initial image prerequisites,
operator-approved provisioning, existing-installation
cutover boundaries, secret delivery, dashboards and alerts.

See [development monitoring](dev-monitoring.md).

## Further reading / acceptance

- [Read-only end-to-end health and diagnostics](dev-health-report.md)
- [Reboot recovery with manual Shamir unseal](dev-reboot-recovery.md)
- [Non-destructive startup idempotency checks](dev-startup-idempotency.md)
- [First-run reliability and performance](dev-initialize-reliability.md)
- [Phase 6E final acceptance checklist](dev-final-acceptance.md)

This document describes the development environment only; it is not a production deployment, automated Auto Unseal, credential rotation plan, or full functional E2E test suite.
