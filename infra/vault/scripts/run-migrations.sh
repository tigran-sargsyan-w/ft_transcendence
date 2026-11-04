#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$ROOT_DIR"

# Prevent concurrent migration workflows on the same host.
if ! command -v flock >/dev/null 2>&1; then
  echo "ERROR: flock is required (util-linux)" >&2
  exit 1
fi

# Use the script file itself as the lock target.
LOCK_FILE="$ROOT_DIR/infra/vault/scripts/run-migrations.sh"

# Reuse the inherited lock descriptor when called by start-backend.sh.
# Otherwise, open our own descriptor for standalone migration runs.
if [[ ! -e /proc/self/fd/9 || ! "$LOCK_FILE" -ef /proc/self/fd/9 ]]; then
  exec 9< "$LOCK_FILE"
fi

if ! flock -n 9; then
  echo "ERROR: Another migration workflow is already running" >&2
  exit 1
fi

echo "Migration workflow lock acquired"

COMPOSE=(
  docker compose
  -f compose.yml
  -f compose.vault.yml
)

cleanup() {
  local status=$?

  trap - EXIT

  echo "=== Cleaning up migration secret ==="

  if ! "${COMPOSE[@]}" \
    --profile vault-maintenance \
    run --rm --no-deps vault-migrator-cleanup; then

    echo "ERROR: Migration secret cleanup failed" >&2

    if [ "$status" -eq 0 ]; then
      status=1
    fi
  fi

  exit "$status"
}

# Always attempt cleanup on normal exit or handled interruption.
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

echo "=== Step 1: PostgreSQL readiness ==="

"${COMPOSE[@]}" \
  up -d --no-deps --wait postgres

echo "=== Step 1.5: Ensure PostgreSQL roles ==="

"${COMPOSE[@]}" exec -T postgres \
  sh -c 'exec psql -X -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
  < "$ROOT_DIR/infra/postgres/ensure-roles.sql"

echo "=== Step 2: Remove previous migration secret ==="

"${COMPOSE[@]}" \
  --profile vault-maintenance \
  run --rm --no-deps vault-migrator-cleanup

echo "=== Step 3: Fresh migration secret delivery ==="

"${COMPOSE[@]}" \
  --profile vault-migration \
  run --rm --no-deps vault-migrator-agent

echo "=== Step 4: Prisma migrate deploy ==="

"${COMPOSE[@]}" \
  --profile vault-migration \
  run --rm --no-deps --build backend-migrate

echo "=== Step 5: Reconcile PostgreSQL grants ==="

"${COMPOSE[@]}" exec -T postgres \
  sh -c 'exec psql -X -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
  < "$ROOT_DIR/infra/postgres/reconcile-grants.sql"

echo "=== Prisma migrations and PostgreSQL grants completed successfully ==="
