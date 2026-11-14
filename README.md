# ft_transcendence

An infrastructure intelligence / real-time incident platform built for the 42 `ft_transcendence` project. The development stack uses a React/Vite frontend, NestJS backend, PostgreSQL/Prisma, Docker Socket Proxy, Collector, Graph Engine, Nginx HTTPS and a separate Vault server with a runtime Vault Agent.

## Development startup (Ubuntu VM)

On an **already initialized, unsealed** development VM, run **one command** from the repository root:

```sh
make up
# Equivalent: ./scripts/bootstrap.sh --start
```

The guarded entry point verifies the existing Vault-backed stack and starts only known, trusted stopped containers. It does not initialize Vault, create database credentials, run Prisma migrations or recreate persistent volumes. Open `https://transcendence.test:8443` in the browser configured for that VM.

First time on a new VM, install/check the required packages and use the **interactive** initialization workflow:

```sh
./scripts/install-deps.sh
make initialize
```

After reboot, single-node Vault using Shamir 2-of-3 normally needs **manual unseal**; run `make recover` from a real terminal, and enter two distinct shares into the hidden Vault CLI prompts. Never provide shares in command arguments, Git, chat, screenshots or logs. This is development-only operator-assisted recovery, **not unattended Auto Unseal**.

Useful commands: `make health` (read-only health/diagnostics), `make check-all` (read-only readiness), `make plan` (resource inventory), `make acceptance` (three non-destructive repeated-start checks on an already healthy VM). Do not use raw `docker compose up`, `make down` or `make clean` to troubleshoot the trusted runtime.

**Start here:** [Development onboarding and operational commands](docs/dev-getting-started.md). For details see [reboot recovery](docs/dev-reboot-recovery.md), [health report](docs/dev-health-report.md), [idempotency acceptance](docs/dev-startup-idempotency.md), and [final acceptance checklist](docs/dev-final-acceptance.md).
