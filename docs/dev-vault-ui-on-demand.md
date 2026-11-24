# Optional on-demand Vault Web UI (development)

The Vault application, its Raft storage, Vault Agents, Backend, Grafana and PostgreSQL
are independent of the browser-facing HAProxy TCP gateway. By default the gateway
is **disabled**. The operator explicitly enables it only when administering Vault.
The HAProxy configuration continues to use TLS passthrough to Vault and publishes
`127.0.0.1:8200` only.

```sh
make vault-ui-status   # read-only; shows ENABLED or DISABLED
make vault-ui-enable   # verifies trusted Vault, creates/resumes only HAProxy
# Open https://localhost:8200/ui/ using the trusted development CA
make vault-ui-disable  # stops only the trusted HAProxy gateway
make health            # accepts DISABLED as expected, never ignores drift
```

`make initialize`, `make start` and `make recover` never provision, resume,
or require the optional HAProxy gateway. `make health` and `make check-all`
verify the **operator's intent**: the presence of a protected 0600 marker in
`$XDG_STATE_HOME/transcendence/dev/vault-ui.enabled` means enabled; without it,
the expected state is disabled. A gateway unexpectedly running while disabled,
or stopped/unhealthy while enabled, fails the health check rather than silently
passing. Enabled intent persists across reboots, but routine start **does not**
automatically start the gateway. If enabled intent remains but HAProxy has
stopped after a reboot, `make start` / `make recover` warn and continue bringing
up the core application. `make health` deliberately reports a failure until the
operator explicitly runs `make vault-ui-enable` or `make vault-ui-disable`.

When upgrading a VM that already has a running gateway, run
`make vault-ui-disable` once to adopt the secure default, then explicitly
`make vault-ui-enable` for any administrative session. Do not run raw
`docker compose up` or `docker stop` to bypass the lifecycle/identity checks.
No operation rotates Vault tokens or reinitializes/unseals Vault. Disabling
HAProxy does not disable the built-in Vault UI on the private network and is
not a substitute for ACL, authentication, TLS, or secure handling of tokens.
