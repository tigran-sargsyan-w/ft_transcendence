#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$ROOT_DIR"

source "$ROOT_DIR/infra/vault/scripts/app-compose.sh"

# Share the migration workflow lock.
if ! command -v flock >/dev/null 2>&1; then
  echo "ERROR: flock is required" >&2
  exit 1
fi

LOCK_FILE="$ROOT_DIR/infra/vault/scripts/run-migrations.sh"

# Reuse an inherited lock when called from run-migrations.sh.
if [[ ! -e /proc/self/fd/9 || ! "$LOCK_FILE" -ef /proc/self/fd/9 ]]; then
  exec 9< "$LOCK_FILE"
fi

if ! flock -n 9; then
  echo "ERROR: Another migration or provisioning workflow is running" >&2
  exit 1
fi

echo "PostgreSQL provisioning lock acquired"

cleanup() {
  local exit_code=$?
  trap - EXIT

  echo "=== Cleaning up PostgreSQL provisioning credentials ==="

  if ! "${COMPOSE[@]}" \
    --profile vault-maintenance \
    run --rm --no-deps -T \
    vault-postgres-provisioner-cleanup; then

    echo "ERROR: PostgreSQL credentials cleanup failed" >&2

    if (( exit_code == 0 )); then
      exit_code=1
    fi
  fi

  exit "$exit_code"
}

trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

echo "=== Step 1: Remove stale provisioning credentials ==="

"${COMPOSE[@]}" \
  --profile vault-maintenance \
  run --rm --no-deps -T \
  vault-postgres-provisioner-cleanup

echo "=== Step 2: PostgreSQL readiness ==="

"${COMPOSE[@]}" \
  up -d --no-deps --wait postgres

echo "=== Step 3: Ensure PostgreSQL roles ==="

"${COMPOSE[@]}" exec -T postgres \
  sh -c 'exec psql -X -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
  < "$ROOT_DIR/infra/postgres/ensure-roles.sql"

echo "=== Step 4: Fetch PostgreSQL credentials from Vault ==="

"${COMPOSE[@]}" \
  --profile vault-setup \
  run --rm --no-deps -T \
  vault-postgres-provisioner-agent

echo "=== Step 5: Reconcile PostgreSQL credentials ==="

python3 "$ROOT_DIR/infra/postgres/provision_credentials.py" --apply-new

echo "=== PostgreSQL credentials provisioning completed successfully ==="
