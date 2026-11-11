#!/usr/bin/env bash

# Shared application Compose invocation for migration/provisioning helpers.
# ROOT_DIR is supplied by the calling script.
if [[ -z "${ROOT_DIR:-}" ]]; then
  echo "ERROR: ROOT_DIR is not defined" >&2
  return 1
fi

FT_APP_COMPOSE_PROJECT="${FT_APP_COMPOSE_PROJECT:-transcendence-app-dev}"
FT_DEV_TLS_DIR="${FT_DEV_TLS_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/transcendence/dev/tls}"
export FT_DEV_TLS_DIR

COMPOSE=(docker compose -p "$FT_APP_COMPOSE_PROJECT" -f "$ROOT_DIR/compose.yml")

if [[ -n "${FT_APP_COMPOSE_OVERRIDE:-}" ]]; then
  if [[ "$FT_APP_COMPOSE_PROJECT" != ft-cr-* ]]; then
    echo "ERROR: runtime Compose overrides are not supported for normal development" >&2
    return 1
  fi

  if [[ ! -f "$FT_APP_COMPOSE_OVERRIDE" ]]; then
    echo "ERROR: Compose override file not found" >&2
    return 1
  fi

  COMPOSE+=(-f "$FT_APP_COMPOSE_OVERRIDE")
fi

if [[ "$FT_APP_COMPOSE_PROJECT" == "transcendence-app-dev" ]]; then
  # A direct helper invocation must not recreate the historical development
  # containers with the new single-file configuration before approved cutover.
  for volume in transcendence-vault-dev_vault_data transcendence-app-dev_postgres_data; do
    if ! docker volume inspect "$volume" >/dev/null 2>&1; then
      echo "ERROR: existing persistent volume missing; startup refused" >&2
      return 1
    fi
  done

  containers="$(docker ps -aq --filter \
    "label=com.docker.compose.project=transcendence-app-dev")" || return 1

  if [[ -z "$containers" ]]; then
    echo "ERROR: existing application containers missing; startup refused" >&2
    return 1
  fi

  while IFS= read -r container; do
    [[ -n "$container" ]] || continue
    provenance="$(docker inspect --format \
      '{{index .Config.Labels "com.docker.compose.project.config_files"}}' \
      "$container")" || return 1

    if [[ "$provenance" != "$ROOT_DIR/compose.yml" ]]; then
      echo "ERROR: legacy application container detected; unified cutover not completed" >&2
      echo "Use the existing containers without Compose recreation until #115 cutover." >&2
      return 1
    fi
  done <<< "$containers"
elif [[ "$FT_APP_COMPOSE_PROJECT" != ft-cr-* ]]; then
  echo "ERROR: unexpected application project" >&2
  return 1
fi
