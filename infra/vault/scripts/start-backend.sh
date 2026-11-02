#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$ROOT_DIR"

COMPOSE=(
  docker compose
  -f compose.yml
  -f compose.vault.yml
)

echo "=== Step 1: Stop existing backend ==="

"${COMPOSE[@]}" stop backend

echo "=== Step 2: Run database migrations ==="

"$ROOT_DIR/infra/vault/scripts/run-migrations.sh"

echo "=== Step 3: Start backend ==="

"${COMPOSE[@]}" up -d --wait backend

echo "=== Vault-backed backend startup completed ==="
