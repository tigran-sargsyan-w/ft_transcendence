# One-time localhost cutover (development)

For an existing healthy, native Ubuntu development installation using the
original `transcendence.test` hostname. This is an explicit maintenance action,
not part of normal startup, recovery or fresh initialization. Run as the
ordinary development user from the project root. Do not run concurrently
with other maintenance or Compose commands.

The canonical application origin becomes `https://localhost:8443` with the
standard ports. Grafana remains under `/grafana/`. Vault UI uses the separate
cookie host `https://127.0.0.1:8200/ui/`; its lifecycle remains on-demand. Do
not use the Vault UI's `localhost` alias because browser cookies are not
isolated by port. The canonical URL does not prohibit alternate TLS aliases.

First validate the existing state without changing containers or configuration:

```sh
PYTHONPATH=.:scripts/dev python3 scripts/dev/migrate_localhost.py plan
```

Only after a successful plan, review the expected short application downtime
and ensure there are no unrelated pending upgrades or maintenance operations.
Apply is explicit:

```sh
PYTHONPATH=.:scripts/dev python3 scripts/dev/migrate_localhost.py apply
```

The helper validates the original and target Compose models, requires a local
Docker socket and healthy original runtime, checks both existing certificates,
verifies the four affected container identities, and refuses non-frontend image
tag drift. It builds only the frontend image before changing the configuration,
then atomically updates `DEV_DOMAIN` while retaining other settings. It recreates
backend, frontend, Grafana and Nginx only, without dependencies or image pulls,
and checks the actual target environment, other container IDs, named volume
inventory and full health. Existing volumes, databases, Vault and agents are
not removed or reinitialized. It does not modify host mappings, install CA trust
or regenerate certificates. The original hosts mapping is intentionally retained
until a separate reviewed cleanup. Existing browser sessions on the original
hostname do not transfer; sign in again at the new origin.

If the original configuration is already `localhost`, this one-time helper
refuses to run. A fresh installation uses the tracked `config/development.env`
with `DEV_DOMAIN=localhost` and ordinary guarded initialization. Changing a
hostname or port again on an existing installation requires a reviewed runtime
update; editing the config alone is not a live reload.

If apply stops after the hostname is written, inspect `config/development.env`
and the four target containers before retrying or deciding on rollback. There
is no automatic rollback or resource deletion. Do not run `make clean`,
`docker compose down -v`, `vault operator init`, or reset the database. Contact
the infrastructure owner for a controlled recovery. Do not expose raw Docker
inspection output, tokens or secret-bearing logs.
