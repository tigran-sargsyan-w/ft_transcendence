# Vault-backed one-command startup: final acceptance (Issue #115 / Phase 6E)

**Scope:** an initialized Ubuntu 26.04 developer VM running the existing Vault-only Compose stack. No destructive outage injection, real production backup/restore, new Auto Unseal provider, password rotation, or full authentication/WebSocket E2E suite is claimed by this work item.

## Acceptance commands

Run as a normal user from the reviewed project checkout on a healthy VM:

```sh
bash -n scripts/bootstrap.sh
python3 -m unittest discover -s scripts/dev -p 'test_bootstrap_unified.py' -q
python3 -m unittest discover -s scripts/dev -p 'test_verify_startup_idempotency.py' -q
python3 -m unittest discover -s scripts/dev -p 'test_makefile_startup.py' -q
make -n up                         # must only print the guarded --start path
make health                        # read-only, whole-stack verification
make up                            # safe routine startup
make check-all
python3 scripts/dev/verify_startup_idempotency.py --check-only
python3 scripts/dev/verify_startup_idempotency.py --runs 3
make health
```

Do not run `make initialize` on an existing VM. Do not reboot or seal Vault just to rerun this acceptance when a successful controlled reboot test is already recorded. If a test fails, stop without automatic repair; investigate using the per-stage read-only checks and preserve non-secret evidence.

## Evidence checklist

| Requirement | Evidence / verification |
| --- | --- |
| One guarded routine command | `make up` must delegate to `./scripts/bootstrap.sh --start`, not directly to Compose. Check with `make -n up` and an actual run. |
| Initial provisioning separated | `make initialize` is interactive; only applicable to a new trusted VM. Its **combined first-install command** is not attested by the repeated-start test. |
| Manual unseal boundary | Sealed Shamir 2-of-3 Vault must require two distinct shares at hidden CLI prompts via `make recover`; no shares on argv or in logs. |
| Safe recovery after restart | The controlled Phase 6B reboot exercised first-attempt `--recover`, subsequent `--check-all` and `--start`. Vault and PostgreSQL Docker IDs matched the pre-reboot snapshot. |
| Vault, roles, DB, migrations, runtime | `make health` transitively verifies Vault topology, KV/ACL/AppRoles, PostgreSQL roles/SCRAM, Prisma checksums/grants, Vault Agent, backend, Collector, Graph Engine, frontend and HTTPS. |
| Idempotency | Phase 6D recorded three consecutive safe `--start` runs without changed container IDs/uptimes, persistent volume or network identities, or Prisma history. Run the acceptance script again when onboarding a different VM. |
| Concurrency / fail-closed | Existing mocked unit tests cover rejected identities, lock contention, missing resources, failed unseal, no silent provisioning and inconsistent Docker state. Real concurrent outage injection is not implied. |
| HTTPS / actual product | Backend API/DB response, trusted TLS and Nginx redirect are checked. **Browser UI and login/WebSocket user flows require a separate manual/functional verification.** |
| Documentation / team usability | Follow [developer onboarding](dev-getting-started.md), [reboot recovery](dev-reboot-recovery.md), [health report](dev-health-report.md) and [idempotency validation](dev-startup-idempotency.md). |

## Sign-off record — 2026-11-15

- Branch: `feat/vault-unified-startup`
- Integration: latest `main` merged successfully, without Git conflicts.
- Offline tests (after integration):
  - Bootstrap/regression: 426/426 PASS
  - Diagnostic/benchmark: 36/36 PASS
- Vault and PostgreSQL: healthy; container identities preserved during backend update.
- Prisma: latest user-profile migration applied successfully; grants verified.
- Backend: rebuilt and updated successfully; health check PASS.
- Startup acceptance: 3/3 PASS; no unexpected recreation, restart, or migration drift.
- Final infrastructure health:
  - `make health` — DEVELOPMENT HEALTHY
  - `make check-all` — UNIFIED STACK READY
- Browser: application opens successfully in Ubuntu VM; HTTPS endpoint returns HTTP 200.

### Known limitations

- Initial provisioning on a completely fresh VM was not repeated in this final acceptance.
- Full authentication, WebSocket and user-flow E2E testing is outside this acceptance.
- Graph Engine was not rebuilt after the latest `main` integration.
- Recovery remains operator-assisted (manual Shamir unseal).
- Development recovery material requires secure, independent off-VM backup.
- Application image upgrades and Prisma migrations require explicit maintenance; routine `make up` intentionally does not perform them.

**Security note:** do not paste `vault-init.json`, root tokens, Shamir shares, DSNs, SecretIDs, secrets from `docker inspect`, or database rows into acceptance logs, pull requests or chat. The acceptance commands do not prove user-data immutability or backup recovery.
