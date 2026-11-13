#!/usr/bin/env bash
# ft_transcendence fresh-machine bootstrap (incremental implementation).
# Phase 1: host TLS/domain. Phase 2: Docker networks/volumes.
# Phase 3: Vault server. Phase 4A: KV v2, ACL policies, AppRoles.
# Phase 4B: initial PostgreSQL credentials stored in Vault KV v2.
# Phase 4C: PostgreSQL 18. Phase 4D: Vault-backed PostgreSQL roles.
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

usage() {
  cat <<'HELP'
Usage: ./scripts/bootstrap.sh [--plan|--prepare-host|--check-host|--prepare-resources|--check-resources|--prepare-vault|--check-vault|--prepare-vault-config|--check-vault-config|--prepare-vault-secrets|--check-vault-secrets|--prepare-postgres|--check-postgres|--prepare-postgres-roles|--check-postgres-roles|--help]

  --plan          Read-only inventory: dependencies, local setup, Docker state.
  --prepare-host  Prepare domain, nginx TLS, local CA trust and Vault TLS.
                  Does NOT start Docker or initialize Vault/PostgreSQL.
  --check-host    Verify host preparation without modifying anything.
  --prepare-resources  Create missing Compose-compatible Docker networks/volumes.
                       Does NOT start containers or initialize Vault/PostgreSQL.
  --check-resources    Read-only verification of Docker networks/volumes.
  --prepare-vault      Start/init a new Vault (DEV ONLY, protected recovery file).
                       Existing initialized Vault is NEVER reinitialized.
  --check-vault        Read-only check of Vault identity and readiness.
  --prepare-vault-config  Configure Vault KV v2, ACL policies and AppRoles (no DB secrets).
  --check-vault-config    Authenticated read-only validation of Vault configuration.
  --prepare-vault-secrets  Create missing PostgreSQL DSNs in Vault KV v2 (no overwrite).
  --check-vault-secrets    Verify both Vault database secrets (read-only).
  --prepare-postgres  Initialize and start PostgreSQL 18, without retaining an admin password.
  --check-postgres    Read-only PostgreSQL container, auth and health verification.
  --prepare-postgres-roles  Initially provision backend/migrator roles from Vault DSNs.
  --check-postgres-roles    Read-only PostgreSQL role state/grants verification.
  --help          Show this message.

This is an INCREMENTAL fresh-machine bootstrap, not a complete installer.
Do not run Docker Compose `up` or `make up` after this step: the existing
runtime is designed for previously initialized infrastructure.
HELP
}

MODE="${1:---plan}"
case "$MODE" in
  --plan|--prepare-host|--check-host|--prepare-resources|--check-resources|--prepare-vault|--check-vault|--prepare-vault-config|--check-vault-config|--prepare-vault-secrets|--check-vault-secrets|--prepare-postgres|--check-postgres|--prepare-postgres-roles|--check-postgres-roles) ;;
  --help|-h) usage; exit 0 ;;
  *) usage >&2; exit 2 ;;
esac
(( $# <= 1 )) || { usage >&2; exit 2; }

say() { printf '%s\n' "$*"; }
fail() { say "ERROR: $*" >&2; exit 1; }
confirm() {
  local answer
  [[ -t 0 ]] || fail "An interactive terminal is required for confirmation"
  printf '%s [y/N] ' "$1"
  IFS= read -r answer || return 1
  [[ "$answer" == 'y' || "$answer" == 'Y' || "$answer" == 'yes' || "$answer" == 'YES' ]]
}

[[ $(id -u) != 0 ]] || fail "Run as a regular user, not with sudo"
[[ -r /etc/os-release ]] || fail "Cannot determine host OS"
# shellcheck disable=SC1091
source /etc/os-release
[[ ${ID:-} == ubuntu ]] || fail "This bootstrap currently supports Ubuntu only"
[[ -x scripts/install-deps.sh ]] || fail "Missing executable scripts/install-deps.sh"
[[ -f infra/vault/server/tls-init.sh ]] || fail "Missing Vault TLS initialization script"
[[ -f scripts/dev/bootstrap_vault.py ]] || fail "Missing Vault bootstrap helper"
[[ -f scripts/dev/bootstrap_vault_config.py ]] || fail "Missing Vault configuration helper"

# Keep the state convention used by issue #115 dev-preflight.py.
TLS_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/transcendence/dev/tls"
CA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/ft-transcendence/vault-ca"
if [[ -n ${FT_DEV_TLS_DIR:-} && ${FT_DEV_TLS_DIR%/} != "${TLS_DIR%/}" ]]; then
  fail "FT_DEV_TLS_DIR differs from the unified runtime's expected TLS path"
fi
export FT_DEV_TLS_DIR="$TLS_DIR"

# Never follow an unexpected symbolic link to host key material.
for path in "$TLS_DIR" "$CA_DIR" \
  "$TLS_DIR/ca.crt" "$TLS_DIR/vault.crt" "$TLS_DIR/vault.key" \
  "$CA_DIR/ca.key"; do
  [[ ! -L "$path" ]] || fail "Refusing symlink in trusted TLS state: $path"
done

vault_tls_status() {
  local present=0 path
  for path in "$TLS_DIR/ca.crt" "$TLS_DIR/vault.crt" \
              "$TLS_DIR/vault.key" "$CA_DIR/ca.key"; do
    if [[ -e "$path" ]]; then
      [[ -f "$path" && -s "$path" ]] || fail "Invalid Vault TLS file: $path"
      (( present += 1 ))
    fi
  done
  if (( present == 0 )); then
    say "TODO: Vault TLS certificate and CA (not generated)"
    return 1
  fi
  (( present == 4 )) || fail "Incomplete Vault TLS state: refuse to overwrite or regenerate keys"
  openssl verify -purpose sslserver -verify_hostname vault \
    -CAfile "$TLS_DIR/ca.crt" "$TLS_DIR/vault.crt" >/dev/null 2>&1 \
    || fail "Vault TLS certificate validation failed"
  openssl x509 -in "$TLS_DIR/vault.crt" -noout -checkend 0 >/dev/null 2>&1 \
    || fail "Vault TLS certificate has expired"
  say "PASS: Existing Vault TLS chain, hostname and expiry"
  return 0
}

host_check() {
  local missing=0
  say "=== Local host setup ==="
  if python3 -m scripts.dev.domains check >/dev/null 2>&1; then
    say "PASS: Local transcendence.test domain"
  else
    say "TODO: Local transcendence.test domain"
    missing=1
  fi
  if python3 -m scripts.dev.certificates check >/dev/null 2>&1; then
    say "PASS: Browser-facing Nginx TLS certificate"
  else
    say "TODO: Browser-facing Nginx TLS certificate"
    missing=1
  fi
  if python3 -m scripts.dev.ca_trust check >/dev/null 2>&1; then
    say "PASS: Local CA trust"
  else
    say "TODO: Local CA trust"
    missing=1
  fi
  if vault_tls_status; then :; else missing=1; fi
  (( missing == 0 ))
}

inventory_docker() {
  say "=== Existing Docker resources (read-only) ==="
  local found=0 label item
  for item in \
    transcendence-vault-dev_vault_data \
    transcendence-vault-dev_backend_agent_auth \
    transcendence-vault-dev_backend_rendered \
    transcendence-app-dev_postgres_data \
    transcendence-app-dev_migrator_vault_auth \
    transcendence-app-dev_migrator_vault_rendered \
    transcendence-app-dev_postgres_provisioner_vault_auth \
    transcendence-app-dev_postgres_provisioner_rendered; do
    if docker volume inspect "$item" >/dev/null 2>&1; then
      say "EXISTS: volume $item"
      found=1
    fi
  done
  for item in \
    transcendence-vault-dev_vault_private \
    transcendence-app-dev_default \
    transcendence-app-dev_collector_control; do
    if docker network inspect "$item" >/dev/null 2>&1; then
      say "EXISTS: network $item"
      found=1
    fi
  done
  # Never print environment variables, mounts or secret content.
  for label in transcendence-app-dev transcendence-vault-dev; do
    if [[ -n $(docker ps -aq --filter "label=com.docker.compose.project=$label") ]]; then
      say "EXISTS: containers owned by $label"
      found=1
    fi
  done
  if (( found == 0 )); then
    say "PASS: No previously managed runtime resources found"
  else
    say "INFO: Existing runtime resources detected; later provisioning must inspect them before changes"
  fi
}

say "=== Fresh Ubuntu bootstrap: host + Docker resources ==="
./scripts/install-deps.sh --check || fail "Dependencies not ready; run ./scripts/install-deps.sh first"

case "$MODE" in
  --plan)
    if host_check; then
      say "RESULT: HOST PREPARATION ALREADY READY"
    else
      say "RESULT: HOST PREPARATION NEEDED"
    fi
    inventory_docker
    python3 scripts/dev/bootstrap_resources.py plan
    python3 scripts/dev/bootstrap_vault.py plan
    say "INFO: Vault config read-only check is available via --check-vault-config"
    python3 scripts/dev/bootstrap_vault_secrets.py plan
    python3 scripts/dev/bootstrap_postgres.py plan
    python3 scripts/dev/bootstrap_postgres_roles.py plan
    say "INFO: Prisma migrations and final table grants follow in a later phase"
    say "RESULT: BOOTSTRAP PLAN COMPLETE (read-only)"
    ;;
  --check-host)
    if host_check; then
      say "RESULT: HOST PREPARATION READY (read-only)"
    else
      say "RESULT: HOST PREPARATION INCOMPLETE (read-only)"
      exit 1
    fi
    ;;
  --check-resources)
    python3 scripts/dev/bootstrap_resources.py check
    ;;
  --prepare-resources)
    host_check || fail "Host preparation incomplete; run --prepare-host first"
    python3 scripts/dev/bootstrap_resources.py plan
    say ""
    say "This stage creates only missing Docker networks and named volumes."
    say "Existing resources will be checked and retained, never removed."
    say "No containers, Vault initialization, database or secrets are created."
    confirm "Create missing Docker networks and volumes on this VM?" || fail "Cancelled; no resources created"
    python3 scripts/dev/bootstrap_resources.py apply
    ;;
  --check-vault)
    python3 scripts/dev/bootstrap_vault.py check
    ;;
  --prepare-vault)
    host_check || fail "Host preparation incomplete; run --prepare-host first"
    python3 scripts/dev/bootstrap_resources.py check || fail "Docker resources incomplete"
    python3 scripts/dev/bootstrap_vault.py plan
    say ""
    say "This stage creates/starts ONLY the trusted Vault container and its permissions helper."
    say "New, uninitialized Vault will be initialized with Shamir 2-of-3."
    say "Root token and all unseal shares will be saved in a local protected DEV file."
    say "This file is sensitive: export it to secure storage OUTSIDE the VM and Git."
    say "PostgreSQL and application containers will NOT be started."
    confirm "Initialize or resume local development Vault on this VM?" || fail "Cancelled"
    python3 scripts/dev/bootstrap_vault.py apply
    ;;
  --check-postgres)
    python3 scripts/dev/bootstrap_postgres.py check
    ;;
  --check-postgres-roles)
    python3 scripts/dev/bootstrap_postgres_roles.py check
    ;;
  --prepare-postgres-roles)
    host_check || fail "Host preparation incomplete"
    python3 scripts/dev/bootstrap_resources.py check || fail "Docker resources incomplete"
    python3 scripts/dev/bootstrap_postgres.py check || fail "PostgreSQL not ready"
    python3 scripts/dev/bootstrap_vault_secrets.py check || fail "Vault database credentials incomplete"
    python3 scripts/dev/bootstrap_postgres_roles.py plan
    say ""
    say "This stage provisions only PostgreSQL backend/migrator roles via existing Vault tooling."
    say "It may create initial AppRole credentials in the dedicated Docker volume."
    say "Existing database role passwords are verified, NEVER changed or rotated."
    say "Prisma migrations and table grants are NOT applied in this stage."
    confirm "Provision PostgreSQL roles using Vault credentials on this VM?" || fail "Cancelled"
    python3 scripts/dev/bootstrap_postgres_roles.py apply
    ;;
  --prepare-postgres)
    host_check || fail "Host preparation incomplete"
    python3 scripts/dev/bootstrap_resources.py check || fail "Docker resources incomplete"
    python3 scripts/dev/bootstrap_vault_secrets.py check || fail "Vault database credentials incomplete"
    python3 scripts/dev/bootstrap_postgres.py plan
    say ""
    say "This stage initializes only a fresh PostgreSQL 18 cluster (if empty)."
    say "Temporary admin credentials are never stored in Compose or Docker environment."
    say "The final PostgreSQL administrator has NO network password."
    say "Existing and partially initialized volumes are NEVER wiped or reinitialized."
    say "App credentials, grants and Prisma migrations are separate later steps."
    confirm "Initialize or resume PostgreSQL on this VM?" || fail "Cancelled"
    python3 scripts/dev/bootstrap_postgres.py apply
    ;;
  --check-vault-secrets)
    python3 scripts/dev/bootstrap_vault_secrets.py check
    ;;
  --prepare-vault-secrets)
    host_check || fail "Host preparation incomplete"
    python3 scripts/dev/bootstrap_resources.py check || fail "Docker resources incomplete"
    python3 scripts/dev/bootstrap_vault_config.py check || fail "Vault configuration incomplete"
    python3 scripts/dev/bootstrap_vault_secrets.py plan
    say ""
    say "This stage creates ONLY missing backend/migrator PostgreSQL DSNs in Vault KV v2."
    say "Credentials are generated inside a temporary private-network helper container."
    say "Existing valid database secrets will never be rotated or replaced."
    say "No PostgreSQL or application containers are started."
    confirm "Create missing PostgreSQL secrets in Vault on this VM?" || fail "Cancelled"
    python3 scripts/dev/bootstrap_vault_secrets.py apply
    ;;
  --check-vault-config)
    python3 scripts/dev/bootstrap_vault_config.py check
    ;;
  --prepare-vault-config)
    host_check || fail "Host preparation incomplete"
    python3 scripts/dev/bootstrap_resources.py check || fail "Docker resources incomplete"
    python3 scripts/dev/bootstrap_vault.py check || fail "Vault is not ready"
    say ""
    say "This stage configures only KV v2, ACL policies and three Vault AppRoles."
    say "No database secrets, PostgreSQL services or application containers are created."
    say "Existing policies or roles with unexpected settings will be preserved and rejected."
    confirm "Configure Vault policies and AppRoles on this VM?" || fail "Cancelled"
    python3 scripts/dev/bootstrap_vault_config.py apply
    ;;
  --prepare-host)
    say ""
    say "This stage can modify /etc/hosts, local CA trust and local TLS files."
    say "It does not create/start Docker containers, networks or volumes."
    confirm "Prepare local domain and TLS on this VM?" || fail "Cancelled; no preparation started"
    python3 -m scripts.dev.domains setup
    python3 -m scripts.dev.certificates setup
    python3 -m scripts.dev.ca_trust setup
    if vault_tls_status; then
      say "KEEP: Existing valid Vault TLS material"
    else
      say "=== Initialize new local Vault CA and certificate ==="
      ./infra/vault/server/tls-init.sh
    fi
    host_check || fail "Host preparation checks did not pass"
    say "RESULT: HOST PREPARATION READY"
    say "NEXT: Infrastructure/bootstrap provisioning (not yet implemented)"
    ;;
esac
