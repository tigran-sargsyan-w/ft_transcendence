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

## Sign-off record (fill using non-secret evidence)

- Reviewed branch and commit SHA: `______________________________`
- WSL and VM offline tests: `______________________________`
- VM `make up`, `make health`, `make check-all`: `______________________________`
- VM repeated-start acceptance: `______________________________`
- Manual browser/UI verification (if performed): `______________________________`
- Outstanding limitations or follow-up issues: `______________________________`

**Security note:** do not paste `vault-init.json`, root tokens, Shamir shares, DSNs, SecretIDs, secrets from `docker inspect`, or database rows into acceptance logs, pull requests or chat. The acceptance commands do not prove user-data immutability or backup recovery.
