# Optional on-demand Prisma Studio (development)

Prisma Studio is a local database administration tool for developers, not an
application feature or a required startup service. It uses a separate Compose
project (`transcendence-prisma-ui-dev`) containing HAProxy and Prisma Studio.
The core application, PostgreSQL, Vault and its agents remain independent.

## Usage and lifecycle

Run these commands from the initialized repository on the trusted development
host as an ordinary user with local Docker access. The backend and its Vault
Agent must be healthy before enabling the UI.

```sh
make prisma-ui-status   # read-only; shows ENABLED or DISABLED
make prisma-ui-enable   # creates, starts or keeps the reviewed UI pair
# Open https://localhost:5555/ using the trusted application development CA
make prisma-ui-disable  # stops Studio, then HAProxy; retains both containers
```

The public commands use the shared V3 progress renderer. Enable and disable
hold the existing startup lock. Repeated enable/disable operations are no-ops
when the requested state is already reached; a normal stop/start preserves
both container IDs. Status checks actual container state and HTTPS readiness
when running. It does not start services or verify the deployed backend image.

Neither `make initialize`, `make up` / `make start`, nor `make recover` starts
Prisma Studio. `make health` and `make check-all` do not check its lifecycle;
use `make prisma-ui-status` for that. Unlike Vault UI, Prisma Studio has no
persistent enabled-intent marker. Its containers have restart policy `no`.
Enable it only when needed and disable it when finished.

## Access and trust boundaries

- The gateway publishes only `127.0.0.1:5555` by default and terminates TLS
  using the existing application certificate and private key mounted read-only.
  Use the printed localhost HTTPS URL; do not bypass certificate verification.
- HAProxy checks the expected Host and Origin, rejects cross-site requests
  identified by the configured rules, and strips Cookie and Authorization
  headers before forwarding to Studio. Studio listens on loopback within the
  shared network namespace and has no separate published port.
- Studio uses the deployed backend image and the existing Vault Agent's
  rendered database URL file (`DATABASE_URL_FILE`), mounted read-only. No
  additional database role, Vault policy, agent or credential is provisioned.
- **Studio is not read-only.** It can read or modify data within the privileges
  of the existing backend PostgreSQL role. A read-only secret mount does not
  make the database connection read-only. Treat displayed data as sensitive.
- The UI has no separate application login or Nginx/ModSecurity protection.
  Local HTTPS and origin restrictions are not user authentication. Use only on
  a trusted development host; do not expose or forward the port to other hosts.

## Configuration and reviewed UI recreation

`DEV_PRISMA_UI_PORT` in `config/development.env` defaults to `5555`. It must
be a valid, distinct development host port. The internal Studio port remains
`5555`; HAProxy terminates HTTPS internally on `5556`.

The enable command refuses an outdated Studio image rather than silently
recreating containers. After a reviewed backend image upgrade, manually remove
only the retained UI containers and enable Studio again. Before changing the
published UI port, disable and remove the existing UI pair **using the old
configuration**. Changes to the reviewed Compose/HAProxy source files also
require reviewing the lifecycle guard and its expected source hashes.

For intentional UI-only recreation, when both known containers exist:

```sh
make prisma-ui-disable && \
  docker rm transcendence-prisma-ui-dev-studio-1 \
            transcendence-prisma-ui-dev-gateway-1
```

If only one retained UI container exists, remove only that verified stopped
container after a successful disable. Do not use `--force`, `-v`, raw Compose
startup, or remove core resources, networks or volumes. Do not run this manual
maintenance concurrently with other infrastructure operations. If the port was
already changed and the identity check refuses the old containers, restore the
previous port before disabling; stop and investigate any other identity refusal.
After the reviewed change, with the backend and Vault Agent healthy:

```sh
make prisma-ui-enable
make prisma-ui-status
```

For an ordinary partial startup or HTTPS failure, try the guarded disable
followed by enable. If the command refuses identity, image or configuration,
review the reported error rather than bypassing its checks.

## Validation (2026-11-27)

At implementation commit `2e7e01e`, the existing running UI passed status,
repeated enable, disable, repeated disable and re-enable with both container
IDs preserved. Browser access was confirmed after restart. Unrelated container
IDs and reported states remained unchanged. All 920 `scripts/dev` offline
tests passed, and `make health` passed all eight stages with Studio both enabled
and disabled. The documented manual removal procedure and subsequent creation
of both UI containers through the lifecycle manager were also verified.
HTTPS readiness passed, unrelated containers remained unchanged, and both
new UI containers were retained in the stopped state after disable.
Stale-image refusal has offline coverage but was not separately exercised
at runtime. Remote CI and team review remain required.
