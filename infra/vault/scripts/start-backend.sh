#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$ROOT_DIR"

# Protect the entire stop -> migrate -> start workflow.
if ! command -v flock >/dev/null 2>&1; then
  echo "ERROR: flock is required" >&2
  exit 1
fi

LOCK_FILE="$ROOT_DIR/infra/vault/scripts/run-migrations.sh"

exec 9< "$LOCK_FILE"

if ! flock -n 9; then
  echo "ERROR: Another migration or backend startup workflow is running" >&2
  exit 1
fi

echo "Backend startup lock acquired"

source "$ROOT_DIR/infra/vault/scripts/app-compose.sh"

echo "=== Step 1: Stop existing backend ==="

"${COMPOSE[@]}" stop backend

echo "=== Step 2: Run database migrations ==="

"$ROOT_DIR/infra/vault/scripts/run-migrations.sh"

echo "=== Step 3: Start backend ==="

"${COMPOSE[@]}" up -d --wait --build backend

echo "=== Vault-backed backend startup completed ==="
