#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
TLS_DIR="${FT_DEV_TLS_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/transcendence/dev/tls}"
export FT_DEV_TLS_DIR="$TLS_DIR"
CA_CERT="$TLS_DIR/ca.crt"

COMPOSE=(
  docker compose
  -p transcendence-vault-dev
  -f "$SCRIPT_DIR/compose.yml"
)

APP_COMPOSE=(
  docker compose
  -p transcendence-app-dev
  -f "$REPO_ROOT/compose.yml"
)

MODE="${1:-}"
CANDIDATE="${2:-}"

fail() {
  echo "ERROR: $*" >&2
  exit 1
}

if [[ "$MODE" != "check" && "$MODE" != "activate" ]]; then
  fail "Usage: tls-activate.sh check|activate .renew.XXXXXXXX"
fi

if [[ ! "$CANDIDATE" =~ ^\.renew\.[A-Za-z0-9_-]+$ ]]; then
  fail "Invalid candidate directory name"
fi

CANDIDATE_DIR="$TLS_DIR/$CANDIDATE"

[[ -d "$CANDIDATE_DIR" && ! -L "$CANDIDATE_DIR" ]] ||
  fail "Candidate directory not found or is a symlink"

for command in docker openssl sha256sum awk; do
  command -v "$command" >/dev/null ||
    fail "Missing command: $command"
done

[[ -f "$CA_CERT" ]] ||
  fail "CA certificate not found"

echo "Validating candidate certificate..."

openssl verify \
  -purpose sslserver \
  -verify_hostname vault \
  -CAfile "$CA_CERT" \
  "$CANDIDATE_DIR/vault.crt" ||
  fail "Certificate validation failed"

cert_public_hash="$(
  openssl x509 -in "$CANDIDATE_DIR/vault.crt" \
    -pubkey -noout |
  openssl pkey -pubin -outform DER |
  sha256sum |
  awk '{print $1}'
)"

key_public_hash="$(
  openssl pkey -in "$CANDIDATE_DIR/vault.key" \
    -pubout -outform DER |
  sha256sum |
  awk '{print $1}'
)"

[[ "$cert_public_hash" == "$key_public_hash" ]] ||
  fail "Certificate and private key do not match"

candidate_fingerprint="$(
  openssl x509 -in "$CANDIDATE_DIR/vault.crt" \
    -outform DER |
  sha256sum |
  awk '{print $1}'
)"

# Resolve the currently running Vault container.
VAULT_CID="$("${COMPOSE[@]}" ps -q vault)"

[[ -n "$VAULT_CID" ]] ||
  fail "Vault container is not running"

VAULT_IP="$(
  docker inspect -f \
    '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' \
    "$VAULT_CID"
)"

[[ "$VAULT_IP" =~ ^[0-9.]+$ ]] ||
  fail "Cannot resolve Vault Docker IPv4 address"

# Read the actual certificate over a verified TLS connection.
remote_fingerprint() {
  openssl s_client \
    -connect "${VAULT_IP}:8200" \
    -servername vault \
    -verify_hostname vault \
    -verify_return_error \
    -CAfile "$CA_CERT" \
    </dev/null 2>/dev/null |
  openssl x509 -outform DER |
  sha256sum |
  awk '{print $1}'
}

echo "Checking live TLS connection..."

original_fingerprint="$(remote_fingerprint)" ||
  fail "Cannot verify the live Vault TLS connection"

"${COMPOSE[@]}" exec -T vault vault status >/dev/null ||
  fail "Vault is not ready"

echo "Candidate fingerprint: $candidate_fingerprint"
echo "Current fingerprint:   $original_fingerprint"

[[ "$candidate_fingerprint" != "$original_fingerprint" ]] ||
  fail "Candidate is identical to the current certificate"

echo "TLS candidate and live connection verified."


# Verify the runtime agent before making any changes.
AGENT_CID="$("${APP_COMPOSE[@]}" ps -q vault-backend-agent)"

[[ -n "$AGENT_CID" ]] ||
  fail "Runtime Agent container not found"

AGENT_HEALTH="$(
  docker inspect -f '{{.State.Health.Status}}' "$AGENT_CID"
)"

[[ "$AGENT_HEALTH" == "healthy" ]] ||
  fail "Runtime Agent is not healthy before activation"

echo "Runtime Agent preflight: healthy"


if [[ "$MODE" == "check" ]]; then
  echo "Check completed. No files were modified."
  exit 0
fi

BACKUP_NAME="backup-$(date -u +%Y%m%dT%H%M%SZ)-$$"
changes_started=false
completed=false

wait_for_fingerprint() {
  local expected="$1"
  local actual

  for _ in {1..15}; do
    actual="$(remote_fingerprint)" || actual=""

    if [[ "$actual" == "$expected" ]]; then
      return 0
    fi

    sleep 1
  done

  return 1
}

rollback() {
  echo "Attempting TLS rollback..." >&2

  "${COMPOSE[@]}" run --rm --no-deps -T \
    vault-tls-files rollback "$BACKUP_NAME" ||
    return 1

  docker kill --signal=HUP "$VAULT_CID" >/dev/null ||
    return 1

  wait_for_fingerprint "$original_fingerprint" ||
    return 1

  echo "Previous TLS certificate restored." >&2
}

on_exit() {
  local result=$?
  trap - EXIT

  if [[ "$completed" != true && "$changes_started" == true ]]; then
    if ! rollback; then
      echo "CRITICAL: Automatic rollback failed." >&2
      echo "Backup name: $BACKUP_NAME" >&2
      echo "Manual recovery is required." >&2
    fi
  fi

  exit "$result"
}

trap on_exit EXIT

echo "Installing TLS candidate..."
echo "Backup name: $BACKUP_NAME"

# Mark before invocation because installation can fail midway.
changes_started=true

"${COMPOSE[@]}" run --rm --no-deps -T \
  vault-tls-files install "$CANDIDATE" "$BACKUP_NAME"

echo "Reloading Vault TLS listener..."

docker kill --signal=HUP "$VAULT_CID" >/dev/null

echo "Verifying the new live certificate..."

wait_for_fingerprint "$candidate_fingerprint" ||
  fail "Vault did not present the new certificate"

"${COMPOSE[@]}" exec -T vault vault status >/dev/null ||
  fail "Vault health verification failed"

AGENT_CID="$("${APP_COMPOSE[@]}" ps -q vault-backend-agent)"

[[ -n "$AGENT_CID" ]] ||
  fail "Runtime Agent container not found"

AGENT_HEALTH="$(
  docker inspect -f '{{.State.Health.Status}}' "$AGENT_CID"
)"

[[ "$AGENT_HEALTH" == "healthy" ]] ||
  fail "Runtime Agent is not healthy"

completed=true

echo
echo "TLS activation completed successfully."
echo "Active fingerprint: $candidate_fingerprint"
echo "Backup: .backups/$BACKUP_NAME"
echo "Vault remained running."
