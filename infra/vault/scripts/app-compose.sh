#!/usr/bin/env bash

# Shared application Compose command.
# ROOT_DIR must be set by the calling script.

if [[ -z "${ROOT_DIR:-}" ]]; then
  echo "ERROR: ROOT_DIR is not defined" >&2
  return 1
fi

COMPOSE=(docker compose)

if [[ -n "${FT_APP_COMPOSE_PROJECT:-}" ]]; then
  COMPOSE+=(-p "$FT_APP_COMPOSE_PROJECT")
fi

COMPOSE+=(
  -f "$ROOT_DIR/compose.yml"
  -f "$ROOT_DIR/compose.vault.yml"
)

if [[ -n "${FT_APP_COMPOSE_OVERRIDE:-}" ]]; then
  if [[ ! -f "$FT_APP_COMPOSE_OVERRIDE" ]]; then
    echo "ERROR: Compose override file not found" >&2
    return 1
  fi

  COMPOSE+=(-f "$FT_APP_COMPOSE_OVERRIDE")
fi
