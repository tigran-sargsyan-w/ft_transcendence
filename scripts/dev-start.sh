#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

case "${1:-}" in
  --check)
    (( $# == 1 )) || exit 2
    exec python3 \
      "$ROOT_DIR/infra/vault/scripts/dev-preflight.py"
    ;;
  --plan)
    (( $# == 1 )) || exit 2
    exec python3 \
      "$ROOT_DIR/infra/vault/scripts/dev-start-runtime.py" plan
    ;;
  --migrate)
    (( $# == 1 )) || exit 2
    exec python3 \
      "$ROOT_DIR/infra/vault/scripts/dev-start-runtime.py" migrate
    ;;
  ""|--start)
    (( $# <= 1 )) || exit 2
    exec python3 \
      "$ROOT_DIR/infra/vault/scripts/dev-start-runtime.py" start
    ;;
  *)
    echo "Usage: ./scripts/dev-start.sh [--check|--plan|--start|--migrate]" >&2
    exit 2
    ;;
esac
