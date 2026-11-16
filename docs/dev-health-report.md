# Development health report (Phase 6C)

On a previously initialized Ubuntu development VM, use:

```sh
./scripts/bootstrap.sh --health-report
```

This is a **read-only** check: no `docker start`, `docker compose up`,
credentials, AppRole provisioning, Shamir unseal or migrations are run.
It returns exit code 0 only when all required readiness checks pass.
Run it as the regular developer user, not with sudo.

## What is checked

The fast path executes the existing trusted host and resource checks, then
`--check-nginx`. Nginx's *transitive* checks verify the full chain:

- Vault Raft initialization/unseal and trusted Compose identity;
- Vault KV v2 configuration, ACL policies, AppRoles and existing DB secrets;
- PostgreSQL 18, roles, SCRAM, Prisma migration checksums and grants;
- Vault Backend Agent rendered-secret accessibility and backend health;
- Docker Socket Proxy, Collector snapshot/health and Graph Engine health;
- React/Vite HTML, module loading and backend proxy;
- Nginx identity, trusted TLS/HTTPS application routes, API/DB health and
  the HTTP-to-HTTPS redirect.

The fast path does not repeat the nested dependency probes 13 times: this
previously caused Docker CLI timeouts on small VMs.

## Diagnostic path

If the fast end-to-end probe fails after three read-only attempts, the command
runs the existing trusted per-stage checks in dependency order and stops at the
**first failing composite check**. Output looks like:

```text
WARN: end-to-end check failed; locating the first failing dependency
PASS: vault
...
FAIL: postgres; inspect --check-postgres
RESULT: UNIFIED BOOTSTRAP STOPPED (no resources removed)
```

To investigate, run the named command **manually**, for example:

```sh
./scripts/bootstrap.sh --check-postgres
```

A stage check may include its own dependencies, so the reported stage is the
first *observable failing check*, not proof that its named service is the
root cause. If all per-stage checks subsequently pass, the report returns
nonzero with an **intermittent readiness** error rather than hiding the
original failure. No raw Docker/stdout/stderr or secret values are emitted by
the reporter.

## Expected usage

```sh
./scripts/bootstrap.sh --check-all
./scripts/bootstrap.sh --health-report
./scripts/bootstrap.sh --start
./scripts/bootstrap.sh --health-report
```

When Vault is sealed after a reboot, the health report correctly **fails**;
restore through the separate interactive `--recover` procedure after checking
recovery material and backups. Never put Shamir shares, root tokens or DSNs in
logs, screenshots, chat, Git or terminal command arguments.

This is an infrastructure and API readiness test, **not** a substitute for
end-to-end login/session, real-time WebSocket, user-journey or security
penetration tests. It does not implement a new unseal provider or rotation.
