# Development VM reboot recovery (Phase 6B)

This runbook applies **only** to the initialized Ubuntu development VM for
`ft_transcendence` using single-node Vault with Raft and **Shamir 2-of-3**.
It is not production Auto Unseal, HA, backup/restore or disaster recovery.
The Vault server never receives a persistent unseal-share mount.

## Security prerequisites

- Ensure two **distinct** valid Shamir shares are available to their authorized
  operator(s), ideally from secure storage **outside the VM**.
- Do not paste shares, Vault root tokens or `vault-init.json` into chat, shell
  command arguments, Git, logs, tickets or screenshots.
- Never use `--initialize`, `docker compose down -v`, `docker volume rm`,
  `vault operator init` or a replacement Vault container to recover a reboot.
- The trusted recovery workflow will **not** initialize/recreate Vault, generate
  credentials, run migrations, rotate keys or retrieve shares from disk.

## Before a controlled reboot

On the existing, working VM, record **non-secret** inventory outside the VM or
in a secure operations notebook:

```sh
./scripts/bootstrap.sh --check-all
docker inspect --format '{{.Name}} {{.Id}}' \
  transcendence-vault-dev-vault-1 transcendence-app-dev-postgres-1
docker volume inspect --format '{{.Name}}' \
  transcendence-vault-dev_vault_data \
  transcendence-app-dev_postgres_data
```

Ensure that the Vault recovery shares have been backed up securely **outside**
the VM and that a tested VM snapshot/backup is available where appropriate.
Never copy the key values into these test logs. A VM snapshot alone is not an
independent off-VM backup if stored on the same disk.

## After reboot

From a real interactive terminal on that Ubuntu VM, with Docker available to
this non-root user:

```sh
./scripts/bootstrap.sh --recover
```

The command validates host and Docker resources, then checks the **existing**
Vault container identity. If stopped, it only starts that same trusted container.
If initialized but sealed, it invokes `docker exec -it ... vault operator unseal`
**without a key argument**. Vault CLI prompts for two different keys with
hidden input. The orchestrator does not read or log the shares. It validates
unseal progress after each input. The shared inline progress view pauses
before Vault CLI owns the controlling terminal and resumes only after its
existing unseal validation succeeds. No renderer reads or captures the
hidden input. A failed or interrupted prompt stops later startup work.
Once Vault is ready, the command resumes only existing trusted application
containers in dependency order and verifies HTTPS health.

When Vault is already unsealed, `--recover` will skip prompts and may simply
report that the stack is ready. The ordinary fast path remains:

```sh
./scripts/bootstrap.sh --start
./scripts/bootstrap.sh --check-all
```

These commands do not create new Vault/AppRole/DB credentials or apply new
migrations. If a container is missing, untrusted, or running but persistently
unhealthy, recovery stops without recreating it.

## Manual fallback

If automatic orchestration is unavailable, a trusted operator can use the
existing Vault CLI prompt directly (do not append a key to the command):

```sh
docker exec -it transcendence-vault-dev-vault-1 vault operator unseal
# Enter the first share in the hidden prompt.
docker exec -it transcendence-vault-dev-vault-1 vault operator unseal
# Enter a different second share in the hidden prompt.
./scripts/bootstrap.sh --start
./scripts/bootstrap.sh --check-all
```

If Vault is sealed but the operator lacks two distinct shares, **stop**.
Do not reset Vault, reinitialize Raft, extract keys from an unsafe location,
or weaken the threshold. If the first key was accepted but the second is
unavailable, recovery can be continued later by an authorized operator.

## Acceptance evidence (no secret values)

After an authorized controlled reboot, record:

1. Vault reports initialized and unsealed with Raft and the expected seal.
2. The existing Vault and PostgreSQL container IDs match the pre-reboot IDs.
3. The same named data volumes are retained, and existing migration/grant
   checks pass without replaying or changing migration history.
4. `--check-all` and two successive `--start` invocations succeed.
5. Browser access via `https://transcendence.test:8443` works, with no backend
   errors. Confirm the site and API are healthy; do not rely on HTTP 200 alone.
6. Any failure message contains no shares, tokens or rendered DSNs.

Do **not** reboot the VM just to test until the offline tests and code review
have passed, recovery shares are available from an independent secure backup,
and all important local work has been saved.

## First-recovery Nginx readiness investigation (#155)

The existing running-but-not-ready Nginx guard can stop the first recovery
following a VM reboot. Issue #155 tracks investigation independently of
CLI presentation (#165). On 2026-11-24, the #165 VM acceptance reproduced
this refusal after successful manual unseal and existing-service resume.
A second guarded recovery succeeded without manual repair; all 14
container IDs were preserved, the optional gateway remained disabled,
and full health passed. This is not proof of a particular readiness race.
The first failure remains a failure; do not interpret the progress count
as an overall success result. Inspect the failing read-only check locally
and follow the existing recovery procedure only after trusted readiness
has been established. Do not recreate resources or bypass identity checks.
No new waiting, retry, restart or provisioning policy is introduced by #165.

### Observed evidence and recovery policy

The operator's filtered Nginx log analysis for the 2026-11-24 recovery
window reported 116 log lines, 26 DNS resolution failures, three
"no live upstreams" errors and three HTTP 502 responses on the frontend
root route. No connection-refused, upstream timeout, WAF rejection or
TLS-error patterns were matched by that filter. Subsequent root,
Vite module and API health responses were HTTP 200. The Docker Nginx
health status had been healthy before recovery, so that status alone
cannot establish end-to-end readiness.

The results support temporary upstream unavailability; they do not
establish the exact DNS cache mechanism, upstream identity for every
log line, or timestamp-by-timestamp causality. No resolver configuration
change is made by this issue.

During explicit `make recover` only, if the existing ordinary read-only
Nginx checks fail and the guarded inspection identifies a trusted
already-running gateway, the orchestrator allows an additional 60-second
wall-clock probe budget with a two-second interval between retryable
results. This is additional to the original check budget, not a bound on
the entire recovery. Each recovery probe validates the pinned full Docker
ID, existing container identity and running state before prerequisites,
before routes and after a successful route check or retryable HTTP 502.
The full existing dependency, TLS/CA, ACL, Nginx syntax, HTTPS route,
response-content and redirect checks must pass before recovery continues.

Only HTTP 502 from the fixed HTTPS routes `/`, `/src/main.tsx` and
`/api/v1/health` is an eligible retry result. HTTP 403, 503, 504, transport
or certificate failures, malformed successful responses, failed
prerequisites and other nonretryable errors stop this additional wait.
An HTTP 502 is not itself proof of a harmless race. The extra wait ends
on deadline expiry, an unexpected result, cancellation or changed
container identity/state. The original checks and their retry policy
are unchanged. The ordinary healthy fast path, stopped-container
resume, initialization, health and maintenance policies are unchanged.

The recovery helper runs read-only probes in a separate local process
group with the remaining deadline as the wait timeout. On timeout or
Ctrl+C the local group is terminated and the child reaped. Process
creation and termination cleanup may add small overhead; no later
readiness attempt is scheduled after the deadline. The renderer receives
only fixed result codes and reviewed route labels, not raw child
output, HTTP bodies, Docker inspection output or credential values.
The existing manual Shamir unseal remains mandatory for sealed Vault.

A retry may display, for example:

```text
WAIT: Nginx HTTPS / returned HTTP 502 (bounded recovery readiness)
```

If the additional wait fails, inspect the existing read-only Nginx
helper locally using `./scripts/bootstrap.sh --check-nginx`. Do not
restart, recreate, reload or weaken the gateway automatically. Do not
share raw logs containing credentials or request data.

### Validation evidence for #155 (2026-11-24)

Tested commit: `a1b95ed`, branch `fix/nginx-recovery-readiness`.
Environment: dedicated existing Ubuntu 26.04 LTS amd64 VirtualBox development
VM, 4 vCPUs and approximately 3.3 GiB guest RAM. These are operator-recorded
results, not a new production availability guarantee.

Offline regression: 915 tests across the development, integration and
benchmark suites; 909 passed, six skipped, zero failures. This includes
18 new regression tests. Targeted suites passed: Nginx bootstrap 65/65,
Nginx recovery 11/11 and unified bootstrap 90/90. The two Nginx suites
were repeated after fault injection (76/76; not additional unique tests).
The healthy fast path was verified with `make up` and `make recover`
(25.0s and 26.0s respectively); the latter skipped manual unseal and
existing-service resume.

#### Controlled VM reboots

Before the fix, the reproduced first recovery stopped at Nginx after
167.2s; the next recovery succeeded in 27.6s. After the fix, seven
independent reboot trials completed on their first recovery invocation:

| Trial | Recovery duration | Final ordinary Nginx check | Result |
| --- | ---: | --- | --- |
| 01 | 199.6s | 3/3 | Ready |
| 02 | 144.7s | 2/3 | Ready |
| 03 | 158.0s | 3/3 | Ready |
| 04 | 153.2s | 3/3 | Ready |
| 05 | 173.7s | 3/3 | Ready |
| 06 | 130.7s | 1/3 | Ready |
| 07 | 135.9s | 2/3 | Ready |

Durations include operator-entered manual unseal. Trials 03–07 also
recorded exit code 0 and successful post-recovery health reports.
The ordinary Nginx check needed a retry in six trials, but none entered
the new additional wait. These runs verify reboot stability, not natural
activation of the new wait. The pre-reboot inventory and final inventory
matched all 14 container IDs.

#### Controlled fault injection on the dedicated VM

With the stack healthy and Vault already unsealed, the operator temporarily
rejected only Nginx's TCP connection to the existing frontend on port 5173
inside the gateway's network namespace. Frontend and other direct
prerequisite checks remained ready. HTTPS verification used the trusted
local CA, without bypassing TLS. The temporary rule had explicit cleanup,
exit/interrupt cleanup and an independent timed fallback. This was a
live, temporary network mutation, not a read-only test of the environment;
no tracked source or Compose configuration was changed. Do not reproduce
this on a shared or production system.

| Scenario | Observed result |
| --- | --- |
| F00 — healthy baseline | Health 8/8; clean tree; 14 container IDs match |
| Hybrid probe | Simulated first retry code followed by a real successful readiness probe |
| F01 — transient real HTTP 502 | Ordinary checks exhausted; two additional root-route 502s; third full probe passed after fault removal; recovery 13/13, exit 0, 105.6s total |
| F02 — persistent real HTTP 502 | Eight additional root-route 502s; ninth probe failed on budget expiry; controlled stop, make exit 2, 122.9s total; HTTP 502 still present before cleanup |
| F02 — after cleanup | Recovery 13/13, exit 0, 35.8s; health and full readiness passed |
| F03.1 — identity rejection | Real child probe accepted correct ID and rejected deliberately incorrect expected ID with code 1; exactly one probe, no orchestrator retry sleep |
| F03.2 — security regressions | Nginx bootstrap 65/65 and recovery 11/11 |
| F04 — final integrity | Container IDs and volume names match baseline; Nginx OUTPUT chain restored to ACCEPT with no injected rule; health 8/8 (21.0s); check-all 8/8 (20.5s); startup idempotency 3/3 |

The F01 and F02 logs demonstrate real HTTPS HTTP 502 classification and
bounded retry behavior through the full `make recover` entry point.
F02 total time includes earlier stages; the nested 60-second wait was
not independently stopwatch-measured. Its budget-expiry result is
supported by the runtime log and offline deadline tests. F03.1 changed
only the expected ID in a local test process, not the actual Docker
container. The first F03.1 harness attempt incorrectly mocked the
shared Python time module; it was rerun successfully with an isolated
orchestrator clock and the actual child probe. No project code change
was needed.

Post-fault comparisons showed no container replacement or volume-name
drift. The final `make acceptance` recorded container, volume, network
and migration inventory at its healthy baseline and confirmed no
recreation, restart or migration-history drift across three guarded
starts. This is not a pre/post-fault byte-level volume comparison or
an independent pre/post-fault migration-history comparison. Existing
Vault/secret/role/migration readiness checks passed; secret values were
not collected or compared.

The original reboot failure's exact DNS/cache timing mechanism remains
unconfirmed. Controlled faults reproduce the relevant HTTP 502 class,
not the original DNS race itself. The fix mitigates that class within
the bounded recovery budget; it does not promise that every future
recovery succeeds. Persistent failure or any nonretryable probe result
still requires operator review. The ordinary procedure remains
`make recover`, followed by `make health` and `make check-all` when
successful. Never bypass trust checks or reinitialize to clear a
readiness failure.
