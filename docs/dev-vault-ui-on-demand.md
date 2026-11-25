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

## Shared V3 progress (Issue #167)

The three public Makefile commands use the same V3 presentation as
initialize, start, recover, health, plan and acceptance. The unused Compact
profile and profile selector were removed; the existing Full layout is
the single layout. Internal shell/helper invocations do not pass
`--progress`, so they retain their original output and cannot start a
second live renderer.

Enable records the operator intent only after the existing configuration
and identity checks, verifies Vault readiness, then creates, starts or
keeps only the optional gateway. Disable verifies identity and isolation,
stops or keeps the gateway, then clears intent and verifies disabled state.
A no-op lifecycle or intent operation is CACHED. Status is read-only,
shows desired and actual state, and treats an absent or stopped gateway
as healthy only when disabled is intended. No extra readiness probes
or recovery operations are added for presentation.

The public final RESULT/STATUS markers and exit codes are unchanged.
Redundant intermediate RESULT markers are retained in verbose output and
in legacy internal calls; ordinary public output reports one final
outcome. The loopback URL is shown only after enabled HTTPS verification
succeeds. Refusals retain their secret-safe ERROR and RESULT markers.

```sh
make vault-ui-status
FT_DEV_BOOTSTRAP_VERBOSE=1 make vault-ui-status
NO_COLOR=1 make vault-ui-status
CI=1 make vault-ui-status
```

For #167 validation, run the offline renderer, gateway security,
lifecycle, progress and Makefile suites. On an already healthy isolated
development VM, record the unrelated container identities before and
after enable/status/enable/disable/status/disable, verify the final
health and check-all results, and restore the original intended UI
state if needed. Do not remove resources or manipulate Vault itself to
exercise an output path. Test missing/unsafe gateway, lock contention
and failed HTTPS using the offline fixtures. Record actual runtime and
CI results only after execution.
