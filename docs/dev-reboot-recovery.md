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

### Validation plan for #155

Run the offline regression suites before any live recovery test.
New tests cover retryable vs nonretryable outcomes, full target identity
checks, changed/stopped/missing/untrusted targets, the wall-clock
deadline, process-group cleanup on timeout/cancellation, ordinary
startup refusal and the unchanged healthy fast path. Runtime acceptance
is pending: one controlled reboot on the existing test VM, manual
unseal, first-attempt recovery result, original container IDs and
post-recovery health. Record whether the new waiting path was actually
exercised; a successful recovery that never enters it is not a live
validation of the retry branch.
