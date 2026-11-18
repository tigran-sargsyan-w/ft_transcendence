#!/usr/bin/env bash
# Disposable workload only: no operations target the application's Compose project.
set -Eeuo pipefail

LAB_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT="ft-demo-lab"

compose() {
  docker compose --project-name "$PROJECT" --file "$LAB_DIR/compose.yml" "$@"
}

# Enable the optional 'worker' profile for commands that manage it.
compose_dynamic() {
  compose --profile discovery "$@"
}

usage() {
  cat <<'HELP'
Usage: ./demo-lab/demo.sh <command> [service]
  up                  Build/start the three baseline mock services
  status              Display this lab's Docker containers and health
  fail [service]      Inject a health failure (default: api; worker if present)
  recover [service]   Clear the failure (default: api)
  stop [service]      Stop one lab container (default: api): status DOWN
  start [service]     Start a previously stopped lab container
  recreate [service]  Force-recreate one lab container, retaining logical service ID
  add worker          Dynamically add an optional mock worker depending on API
  remove worker       Remove only the optional worker container
  reset               Restore baseline db/api/web; remove worker, clear failures
  logs                Follow lab container logs
  down                Remove only ft-demo-lab containers + its network (no volumes)
  help                Show this help

Monitor: node backend/scripts/demo-watch.mjs --analyze
All changes are scoped to the ft-demo-lab Docker Compose project.
HELP
}

require_service() {
  case "${1:-}" in
    db|api|web|worker) ;;
    *) echo "Only disposable lab services are allowed: db, api, web, worker" >&2; exit 2 ;;
  esac
}

require_existing() {
  local service="$1" id
  id="$(compose_dynamic ps -q --all "$service")"
  if [[ -z "$id" ]]; then
    echo "Lab service $service does not exist. Run 'demo.sh up' first (or 'add worker')." >&2
    exit 1
  fi
}

require_running() {
  local service="$1" running
  running="$(compose_dynamic ps --status running --services)"
  if ! grep -Fxq -- "$service" <<< "$running"; then
    echo "Lab service $service is not running. Run 'demo.sh up' first (or 'add worker')." >&2
    exit 1
  fi
}

failure_flag() {
  local operation="$1" service="$2"
  require_service "$service"
  require_running "$service"
  if [[ "$operation" == fail ]]; then
    compose_dynamic exec -T "$service" sh -c 'touch /run/demo/fail'
    echo "Injected demo-only health failure: $service (allow healthcheck cycles)."
  else
    compose_dynamic exec -T "$service" sh -c 'rm -f /run/demo/fail'
    echo "Recovered demo health: $service (allow healthcheck cycles)."
  fi
}

case "${1:-help}" in
  up)
    [[ $# -eq 1 ]] || { usage >&2; exit 2; }
    # Default Compose profile: the worker is deliberately absent at baseline.
    compose up -d --build --wait --wait-timeout 120
    ;;
  status)
    [[ $# -eq 1 ]] || { usage >&2; exit 2; }
    compose_dynamic ps --all
    for service in db api web worker; do
      id="$(compose_dynamic ps -q --all "$service")"
      if [[ -n "$id" ]]; then
        health="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}not-configured{{end}}' "$id")"
        printf '%s: %s\n' "$service" "$health"
      fi
    done
    ;;
  fail|recover)
    [[ $# -le 2 ]] || { usage >&2; exit 2; }
    failure_flag "$1" "${2:-api}"
    ;;
  stop)
    [[ $# -le 2 ]] || { usage >&2; exit 2; }
    service="${2:-api}"
    require_service "$service"
    require_existing "$service"
    compose_dynamic stop "$service"
    ;;
  start)
    [[ $# -le 2 ]] || { usage >&2; exit 2; }
    service="${2:-api}"
    require_service "$service"
    require_existing "$service"
    compose_dynamic start "$service"
    ;;
  recreate)
    [[ $# -le 2 ]] || { usage >&2; exit 2; }
    service="${2:-api}"
    require_service "$service"
    require_existing "$service"
    # --no-deps prevents Compose from recreating the trusted app or dependencies.
    compose_dynamic up -d --no-deps --force-recreate --wait --wait-timeout 120 "$service"
    ;;
  add)
    [[ $# -eq 2 && "$2" == worker ]] || { usage >&2; exit 2; }
    require_running api
    # A distinct service is added with a real Compose depends_on edge to API.
    compose_dynamic up -d --no-deps --wait --wait-timeout 120 worker
    ;;
  remove)
    [[ $# -eq 2 && "$2" == worker ]] || { usage >&2; exit 2; }
    compose_dynamic rm --stop --force worker
    ;;
  reset)
    [[ $# -eq 1 ]] || { usage >&2; exit 2; }
    require_existing db
    require_existing api
    require_existing web
    # Make baseline repeatable even after a hard-stop scenario.
    compose_dynamic rm --stop --force worker
    compose start db api web
    for service in db api web; do
      failure_flag recover "$service"
    done
    echo 'Baseline restored; wait for all three Docker healthchecks to turn healthy.'
    ;;
  logs)
    [[ $# -eq 1 ]] || { usage >&2; exit 2; }
    compose_dynamic logs --tail 30 --follow
    ;;
  down)
    [[ $# -eq 1 ]] || { usage >&2; exit 2; }
    compose_dynamic down
    ;;
  help|-h|--help)
    [[ $# -eq 1 ]] || { usage >&2; exit 2; }
    usage
    ;;
  *) usage >&2; exit 2 ;;
esac
