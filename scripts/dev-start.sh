#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

case "${1:-}" in
  --check)
    if (( $# != 1 )); then
      echo "Usage: ./scripts/dev-start.sh --check" >&2
      exit 2
    fi
    exec python3 "$ROOT_DIR/infra/vault/scripts/dev-preflight.py"
    ;;
  *)
    echo "Unified startup is not enabled yet (issue #115, phase 1)." >&2
    echo "Available read-only command: ./scripts/dev-start.sh --check" >&2
    exit 2
    ;;
esac
