#!/bin/sh
set -eu

# Local development mode: existing DATABASE_URL workflow.
if [ "${DATABASE_URL_FILE+x}" != "x" ]; then
  exit 0
fi

# Vault mode: require an explicit secret file.
if [ -z "$DATABASE_URL_FILE" ]; then
  echo "DATABASE_URL_FILE must not be empty" >&2
  exit 1
fi

# Prevent using environment credentials in Vault mode.
if [ -n "${DATABASE_URL:-}" ]; then
  echo "DATABASE_URL must not be set in Vault mode" >&2
  exit 1
fi

echo "Waiting for database secret file..."

attempt=0

until [ -f "$DATABASE_URL_FILE" ] &&
      [ -r "$DATABASE_URL_FILE" ] &&
      [ -s "$DATABASE_URL_FILE" ]; do

  if [ "$attempt" -ge 30 ]; then
    echo "Database secret file is unavailable" >&2
    exit 1
  fi

  attempt=$((attempt + 1))
  sleep 1
done

echo "Database secret file is available"