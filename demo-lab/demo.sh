#!/usr/bin/env bash
# All mutations are strictly scoped to the independent ft-demo-lab project.
set -Eeuo pipefail

LAB_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT="ft-demo-lab"

compose() {
  docker compose --project-name "$PROJECT" --file "$LAB_DIR/compose.yml" "$@"
}

usage() {
  cat <<'HELP'
Usage: ./demo-lab/demo.sh <command> [service]
  up                 Build/start only disposable demo containers
  status             Display demo containers and Docker health state
  fail [db|api|web]  Inject failure (default: api) into demo container
  recover [service]  Remove failure flag (default: api)
  reset              Recover all lab services
  logs               Follow lab logs
  down               Remove only this lab's containers and network (no volumes)
  help               Show help

For real Nest events: node backend/scripts/demo-watch.mjs --analyze
Run from the repo root or use the script's absolute path.
HELP
}

require_service() {
  case "${1:-}" in
    db|api|web) ;;
    *) echo "Only these disposable services are allowed: db, api, web" >&2; exit 2 ;;
  esac
}

require_running() {
  local service="$1"
  local running
  running="$(compose ps --status running --services)"
  if ! grep -Fxq -- "$service" <<< "$running"; then
    echo "Demo service $service is not running. Run 'demo.sh up' first." >&2
    exit 1
  fi
}

set_failure_flag() {
  local action="$1" service="$2"
  require_service "$service"
  require_running "$service"
  if [[ "$action" == fail ]]; then
    compose exec -T "$service" sh -c 'touch /run/demo/fail'
    echo "Injected demo-only failure: $service (allow a few healthcheck cycles)."
  else
    compose exec -T "$service" sh -c 'rm -f /run/demo/fail'
    echo "Recovered demo service: $service (allow a few healthcheck cycles)."
  fi
}

case "${1:-help}" in
  up)
    [[ $# -eq 1 ]] || { usage >&2; exit 2; }
    compose up -d --build --wait --wait-timeout 120
    ;;
  status)
    [[ $# -eq 1 ]] || { usage >&2; exit 2; }
    compose ps --all
    for service in db api web; do
      container="$(compose ps -q --all "$service")"
      if [[ -n "$container" ]]; then
        health="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}not-configured{{end}}' "$container")"
        printf '%s: %s\n' "$service" "$health"
      fi
    done
    ;;
  fail|recover)
    [[ $# -le 2 ]] || { usage >&2; exit 2; }
    set_failure_flag "$1" "${2:-api}"
    ;;
  reset)
    [[ $# -eq 1 ]] || { usage >&2; exit 2; }
    for service in db api web; do
      set_failure_flag recover "$service"
    done
    ;;
  logs)
    [[ $# -eq 1 ]] || { usage >&2; exit 2; }
    compose logs --tail 30 --follow
    ;;
  down)
    [[ $# -eq 1 ]] || { usage >&2; exit 2; }
    compose down
    ;;
  help|-h|--help) usage ;;
  *) usage >&2; exit 2 ;;
esac
