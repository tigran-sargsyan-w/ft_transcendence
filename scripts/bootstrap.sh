#!/usr/bin/env bash
# ft_transcendence fresh-machine bootstrap (incremental implementation).
# Phase 1: host TLS/domain. Phase 2: Docker networks/volumes.
# Phase 3: Vault server and development-only initialization.
# PostgreSQL initialization is NOT implemented in this version.
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

usage() {
  cat <<'HELP'
Usage: ./scripts/bootstrap.sh [--plan|--prepare-host|--check-host|--prepare-resources|--check-resources|--prepare-vault|--check-vault|--help]

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
  --help          Show this message.

This is an INCREMENTAL fresh-machine bootstrap, not a complete installer.
Do not run Docker Compose `up` or `make up` after this step: the existing
runtime is designed for previously initialized infrastructure.
HELP
}

MODE="${1:---plan}"
case "$MODE" in
  --plan|--prepare-host|--check-host|--prepare-resources|--check-resources|--prepare-vault|--check-vault) ;;
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
    say "INFO: PostgreSQL provisioning is not implemented in this phase"
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
