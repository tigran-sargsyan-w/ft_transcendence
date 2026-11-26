# ft_transcendence

An infrastructure intelligence / real-time incident platform built for the 42 `ft_transcendence` project. The development stack uses a React/Vite frontend, NestJS backend, PostgreSQL/Prisma, Docker Socket Proxy, Collector, Graph Engine, Nginx HTTPS and a separate Vault server with a runtime Vault Agent. Operational monitoring uses Prometheus and Grafana with a dedicated Vault Agent.

## Operational monitoring

Prometheus collects real Collector and monitoring pipeline metrics.
Grafana provides two provisioned custom dashboards, historical
metrics and visibility into alert states.

- Grafana: the configured application origin followed by `/grafana/`.
- [Monitoring architecture, operations and evaluation](docs/dev-monitoring.md)

Monitoring is included in the guarded startup and recovery workflow.

## Development startup (Ubuntu VM)

On an **already initialized, unsealed** development VM, run **one command** from the repository root:

```sh
make up
# Equivalent: ./scripts/bootstrap.sh --start
```

The guarded entry point verifies the existing Vault-backed stack and starts only known, trusted stopped containers. It does not initialize Vault, create database credentials, run Prisma migrations or recreate persistent volumes. Open the URL printed by `python3 -m scripts.dev.configuration origin` in the browser configured for that VM.

Public development settings are tracked in [`config/development.env`](config/development.env). No root `.env` is required by guarded startup; secrets remain in Vault and protected bootstrap state. Configuration changes require a reviewed runtime cutover; `make up` does not recreate existing containers.

For a new Ubuntu development VM, clone the repository, ensure Git and
GNU Make are available, then run **one guarded first-install command**:

```sh
make initialize
```

It prepares missing system/Docker prerequisites and required images, then
runs the guarded host, Vault, database, application and monitoring stages.
No bootstrap approval prompts are required on supported native Ubuntu Linux;
normal sudo authentication may still be necessary. Docker group membership
may be configured automatically; log out and back in if instructed. Interactive
terminals show live stage progress; CI and redirected logs are plain text.

Do not use `sudo make initialize`. Existing installations and upgrades
require separate review; see [localhost cutover](docs/development/localhost-cutover.md) for the original hostname; initial setup is not a replacement for recovery.

After reboot, single-node Vault using Shamir 2-of-3 normally needs **manual unseal**; run `make recover` from a real terminal, and enter two distinct shares into the hidden Vault CLI prompts. Never provide shares in command arguments, Git, chat, screenshots or logs. This is development-only operator-assisted recovery, **not unattended Auto Unseal**.

Vault Web UI is [optional and on-demand](docs/dev-vault-ui-on-demand.md): `make vault-ui-enable`, `make vault-ui-disable`, `make vault-ui-status`.

Useful commands: `make health` (read-only health/diagnostics), `make check-all` (read-only readiness), `make plan` (resource inventory), `make acceptance` (three non-destructive repeated-start checks on an already healthy VM). Do not use raw `docker compose up`, `make down` or `make clean` to troubleshoot the trusted runtime.

**New to the project?** [Team quick start — Vault](docs/dev-team-quickstart.md) (short version) · [Developer commands](COMMANDS.md) (command reference).

**Operator documentation:** [Development onboarding and operational commands](docs/dev-getting-started.md). For details see [reboot recovery](docs/dev-reboot-recovery.md), [health report](docs/dev-health-report.md), [idempotency acceptance](docs/dev-startup-idempotency.md), and [final acceptance checklist](docs/dev-final-acceptance.md).
