# Read-only dependency graph — P7 review

The complete check is not 13 independent checks run sequentially: the normal
`--check-nginx` entry point recursively executes each prerequisite's check.
The **healthy path is mostly a tree, not a repeated DAG**. Do not implement a
"cache completed checks" optimization without first demonstrating duplicates.

```text
make health / make check-all
  +-- host check (independent)
  +-- Docker resource check (independent)
  `-- nginx check
       `-- frontend check
            `-- support-services check
                 `-- backend check
                      `-- backend-agent check
                           `-- migrations check
                                `-- postgres-roles check
                                     +-- postgres check
                                     `-- vault-secrets check
                                          `-- vault-config check
                                               `-- vault check
```

The nested sequence deliberately covers trusted Docker identity, runtime
readiness, Vault seal and topology, Vault ACL/AppRoles and KV credentials,
least-privilege PostgreSQL roles, Prisma migration/grant state, Vault Agent
credential file permissions, backend/service HTTP readiness, frontend, and
HTTPS/TLS/Nginx. Individual `--check-*` commands must remain usable on their
own and **must not skip their dependencies**. Host and resource preflight are
also checked before the end-to-end chain in the unified orchestrator.

## What profiling established

- Six directly profiled `--check-nginx` runs: 8.55–9.15 seconds; median 8.87 s.
- A different run first failed after 133.44 s, then passed in 8.62 s. This
  indicates intermittent failures, not a verified permanent 9-second bottleneck.
- Vault config helper (`docker compose`) takes around 1.9–2.1 s, Vault secrets
  helper (`docker run`) around 1.1–1.4 s in the healthy samples.
- All six runs performed approximately 53 tracked Python subprocess calls.
  Bash commands and commands which bypass Python instrumentation are not fully
  represented in this count.

## Optimization guardrails

1. No skip/caching of security checks as a shortcut for warm startup. A stale
   success could wrongly mark a compromised or unready system healthy.
2. Preserve explicit `check` behavior of standalone stages. Don't silently
   turn deep health checks into shallow `docker ps` checks.
3. Identify and measure *actual* redundant work (for example, repeated host
   dependency preflights or TLS verification), then optimize the duplicate at
   its owner boundary without weakening validation.
4. Keep retries read-only, bounded, and visible when intermittent failures
   recover. Never silently hide a failed check in a success-only summary.
5. Before merging, exercise negative conditions (missing/unsafe Docker
   resources, invalid AppRole permissions, sealed Vault, unexpected migration
   history, inaccessible backend, failed TLS) using offline mocks or an
   isolated disposable VM. Do not intentionally break the working shared VM.

This document and `test_dependency_check_contract.py` are a **review gate**,
not an acceleration claim. P7 changes neither runtime behavior nor timing.
