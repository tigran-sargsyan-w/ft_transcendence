# Vault Clean-Room Test Harness

This directory contains reusable components for testing the
ft_transcendence Vault/PostgreSQL integration in an isolated
Docker Compose environment.

## Purpose

- Isolate application and Vault Docker Compose projects.
- Prevent reuse of normal-project persistent volumes.
- Keep Vault networking and TLS material separate.
- Verify isolation before running infrastructure tests.
- Support future work on credential rotation, recovery,
  and unified application startup.

## Files

- `preflight.py`: validates Clean-Room isolation conditions.
- `app.override.yml.example`: application Compose override template.
- `vault.override.yml.example`: Vault Compose override template.
- `app.override.yml`: local generated/copied override (Git-ignored).
- `vault.override.yml`: local generated/copied override (Git-ignored).

## Reuse

Create a dedicated environment using distinct project names,
isolated Docker volumes, private networks, TLS material, and
credentials. Do not reuse production or ordinary development
Vault data.

Copy the example override files to their local `.override.yml`
counterparts, then configure the environment variables required
by the overrides and `preflight.py`.

In particular, `FT_CR_TLS_DIR` must refer to the isolated TLS
directory belonging to that test environment.

For Issue #105, the **three AppRole rotation helpers** must also have their CA
mounts overridden to the isolated TLS directory. The current
`*.override.yml.example` files include those entries, and `preflight.py`
verifies them. If you already have local `*.override.yml` files, **merge
those entries without overwriting your existing local configuration**.

Run `preflight.py` before any infrastructure test and again
afterward to confirm isolation.

**Important:** The templates and preflight script do not
initialize Vault, provision credentials, create all required
Docker resources, or automatically run integration tests.
Those operations require a separately reviewed bootstrap
procedure.

## Security

Never commit the local overrides, environment files,
root/admin tokens, SecretIDs, Shamir shares, PostgreSQL
credentials, certificates' private keys, or Raft snapshots.

Do not use `docker compose down -v` without confirming the
exact Clean-Room project and intended persistent volumes.

The existing local test environment must not be recreated
or deleted merely to prepare these templates for Git.

## Compatibility with the unified runtime

The normal development stack now uses a single `compose.yml` plus the
separate Vault server file. Clean-Room override examples remain test-only.
Before building the test Compose model, `preflight.py` binds
`FT_DEV_TLS_DIR` to the independently verified `FT_CR_TLS_DIR`.
An inherited mismatching TLS source is rejected, preventing accidental
reuse of the development CA. The Clean-Room project must keep its own
project names, credentials, networks and volumes.
