# Issue #107 — Host-assisted Shamir unseal (local development)

**Scope:** opt-in, operator-triggered unseal of the existing single-node development Vault in WSL2, using an unlocked GNOME **login** Keyring. This is **not** production Auto Unseal, unattended boot, an HA setup, or a whole-stack startup manager (see #115).

**Implementation:** `infra/vault/scripts/dev-unseal.py` (`doctor`, `setup`, `enroll`, `unseal`, `forget`) and `dev_unseal_workflow.py`. This runbook describes the known local profile, not arbitrary Compose deployments.

## Threat model and limitations

- The GNOME login Keyring stores **two of three** Shamir unseal shares on **the same host**, eliminating independent custody for those two shares if that host and Keyring are compromised. Protect the operating-system account, Keyring password, Docker access, and workstation. Docker group access is effectively privileged.
- Keep independently protected manual recovery shares **outside this Keyring and host**. Loss or corruption of Keyring, a locked desktop session, or lost access to the host must not prevent manual recovery.
- This is an **opt-in manual command**. It does not run on every boot or automatically unlock the login Keyring. After WSL2 restart, the Secret Service may not be running or the login collection may be locked.
- Secrets must never enter Git, Docker images, Compose environment variables, command arguments, shell history, tickets/chat, or logs. The helper passes shares via process stdin to `secret-tool` and to a disposable HTTPS client. Plaintext necessarily exists temporarily in host process memory and client memory: the helper cannot guarantee secure memory zeroization.
- The ephemeral client uses the existing local backend image, a read-only CA-certificate mount, the private Vault network and TLS verification for host `vault`; **no Vault TCP port is published to the host and no shares are permanently mounted into Vault Server**.
- Local profile is pinned to `transcendence-vault-dev-vault-1`, Raft volume `transcendence-vault-dev_vault_data`, the private network `transcendence-vault-dev_vault_private`, and **2-of-3 Shamir**. Enrollment is associated with the volume creation identity. Replacing that volume requires a new approved initialization/recovery plan; *never* reinitialize because unseal failed.

## Existing environment and requirements

From the repository root, on the same WSL2 Linux user:

```bash
BASE="$HOME/.local/state/transcendence/dev"
HELPER="infra/vault/scripts/dev-unseal.py"
# Requires Python 3.10+, docker, docker compose, secret-tool, busctl,
# GNOME Keyring's Secret Service, and local backend image for the HTTPS client.
python3 "$HELPER" --tls-dir "$BASE/tls" doctor
```

`doctor` reports prerequisite availability but does not modify the Keyring or Vault. The expected certificate is `$BASE/tls/ca.crt`. The helper requires an initialized **existing** Vault and its original Raft volume.

If the persistent login Keyring needs to be prepared or unlocked, run **interactively**:

```bash
python3 "$HELPER" setup
```

`setup` can replace the GNOME Keyring daemon and interrupt other Secret Service clients; read its warning and opt in deliberately. Never put the Keyring password in command-line flags or shell history. If the default collection is unexpected, investigate rather than replacing it blindly.

## One-time opt-in enrollment

Only when Vault is **already unsealed** and independent manual shares have been safely retained:

```bash
set +x
umask 077
python3 "$HELPER" --tls-dir "$BASE/tls" enroll
```

Confirm `ENROLL`, enter **two different** genuine shares using masked prompts, and confirm each. Expected: `PASS: Two development shares enrolled in persistent GNOME Keyring`.

Enrollment verifies format, distinctness, Keyring persistence and volume association; it **cannot** validate share correctness against an already-unsealed Vault. The subsequent controlled unseal test is required. Never print, copy into a terminal argument, or share their values.

## Normal operation after a Vault restart

1. Confirm the **same** Vault container is running, initialized and sealed; confirm it still uses the original Raft volume. Do not run `vault operator init`.
2. Ensure the GNOME **login** collection is unlocked. If necessary, use the interactive `setup` procedure above.
3. Run:

   ```bash
   python3 "$HELPER" --tls-dir "$BASE/tls" unseal
   ```

4. Expected success when sealed: `PASS: Existing Vault unsealed through verified HTTPS`. Independently verify:

   ```bash
   docker exec transcendence-vault-dev-vault-1 vault status
   ```

   Look for `Initialized: true`, `Sealed: false`, and `HA Mode: active`. `active` on a single node **does not** imply high availability.
5. Restore the pre-existing app containers **only after Vault has unsealed**, checking each healthcheck and the HTTPS application endpoint. Do not recreate data volumes, auto-run migrations to fix a seal issue, or substitute another project/volume.

A second `unseal` while Vault is already unsealed must return `PASS: Vault already unsealed; no shares accessed`. The helper checks the status **before reading** Keyring shares.

## Failure handling / manual fallback

| Symptom | Safe operator response |
| --- | --- |
| GNOME Keyring unavailable or locked | Do **not** re-enroll blindly. Check Secret Service and interactively unlock the existing login collection. Manual unseal remains supported. |
| Wrong/absent shares or wrong volume creation identity | Stop automatic retries. Check the existing volume/project. Recover using independent manual shares; investigate before any new enrollment. |
| Vault not initialized, unexpected seal type, or unexpected 2-of-3 configuration | **Stop.** Verify container and mounted Raft volume; never initialize or replace data to make the helper proceed. |
| Partial unseal progress | Helper deliberately stops. Investigate and complete/reset through the documented **manual** Vault procedure with an authorized operator, not a blind automated retry. |
| TLS/CA problem, Vault unreachable, or private Docker network missing | Investigate certificate, hostname `vault`, client image and Docker network. Never disable TLS verification or publish Vault's API port as a workaround. |
| Automatic unseal fails after one share | Do not repeatedly submit shares. Check state with `vault status` and use the manual fallback below. |

**Manual fallback (existing Vault only):**

```bash
docker exec -it transcendence-vault-dev-vault-1 vault operator unseal
docker exec -it transcendence-vault-dev-vault-1 vault operator unseal
docker exec transcendence-vault-dev-vault-1 vault status
```

Enter each independently protected share **interactively at the prompt**, never as a CLI argument. Depending on current unseal progress, an operator may need to inspect/reset that progress instead of submitting both commands. If the Vault container is stopped, inspect the mounted Raft volume and start the *existing* container first. Never run `vault operator init`, `docker compose down -v`, or a broad Docker volume prune to recover a sealed Vault.

## Disable or delete the local helper's shares

No systemd service or startup hook is installed: **stop invoking** `unseal` to disable this opt-in workflow. To remove the enrolled shares, unlock the login Keyring and run:

```bash
python3 "$HELPER" forget
```

Type `FORGET` to confirm deletion. Verify removal through a safe metadata/lookup check that does **not** print contents; if Keyring access fails, investigate before claiming removal succeeded. Deleting the enrollment does not seal Vault or erase Raft data. Preserve manual recovery shares until Vault is deliberately decommissioned.

## WSL2 full-restart acceptance test (passed 2026-11-10)

**Before testing:** confirm independent manual recovery shares, an authorized downtime window, current working stack, and exact Docker/WSL ownership. `wsl --shutdown` (issued from Windows) stops **all** WSL distributions and may affect Docker Desktop and unrelated workloads. **Do not run it as part of this helper or without inspecting other workloads first.**

Repeatable procedure, validated on 2026-11-10 with `wsl --terminate Ubuntu`:

1. Record current WSL distribution name, Docker context, container names/IDs, Raft volume, health and encrypted backup existence. Do not dump credentials, environment variables or keyring items.
2. Cleanly stop the eight application containers and original Vault container; preserve containers and volumes. Confirm expected state.
3. Only when unrelated WSL/Docker workloads are accounted for, shut down WSL2 from Windows; reopen the **same distribution** and ensure Docker is reachable.
4. Inspect whether Vault/app containers restarted automatically. Before touching shares, verify original Raft volume, `Initialized: true` and actual `Sealed` state. Avoid accidentally starting app services before Vault is ready.
5. If sealed, check GNOME Secret Service/login collection and interactively unlock it if necessary. Run `unseal` and verify Vault is active/unsealed. If it fails, stop and use the manual fallback; **do not** reinitialize Vault.
6. Start/recover the original eight application containers in dependency order, with health checks, TLS frontend and `/api/v1/health` probes; verify no data volume was replaced.
7. Record only non-secret evidence (container IDs, status/health, test outcomes). Mark WSL2 restart as tested **only after** these observations succeed.

## Evidence (2026-11-10)

- 37 offline unit tests passed after the explicit GNOME login collection-path regression test.
- Live enrollment wrote two distinct shares, and read-back verified both entries were accessible and matched the expected Raft volume identity; contents were never displayed.
- The ephemeral HTTPS client successfully queried an already-unsealed Vault without Keyring access.
- A controlled restart of the original Vault container left it initialized/sealed; the helper unsealed it through verified HTTPS using Keyring shares; Vault became active and unsealed.
- All eight original application containers were restarted without recreation. The nine-container stack recovered; checked Docker health, HTTPS frontend, Backend API, PostgreSQL, eight protected volumes and the pre-existing encrypted #106 backup all passed.
- Repeated `unseal` was verified to avoid Keyring access. Simulated locked Keyring, partial progress, wrong threshold and uninitialized-Vault paths all stopped safely.
- **WSL2 cold-start passed:** Ubuntu was terminated and restarted; Docker Engine and containerd recovered through systemd. All ten original containers and eight protected volumes survived. Secret Service required D-Bus activation, followed by interactive unlock of the existing GNOME login Keyring. The original Vault restarted sealed (2-of-3), was unsealed through the helper, and returned active. All ten containers were recovered. Frontend HTTPS, Backend API, PostgreSQL health, Collector snapshot and Engine.IO WebSocket handshake passed. This remains operator-assisted, not unattended Auto Unseal.
- **Deletion validation:** `forget` is covered by mocked unit tests. Real Keyring entry deletion was intentionally not performed, to preserve the working enrollment.
