#!/usr/bin/env bash
set -Eeuo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/../.."

NAME="ft-pr133-acl-regression-$$"
IMAGE="${FT_TEST_POSTGRES_IMAGE:-postgres:18-bookworm}"

if docker container inspect "$NAME" >/dev/null 2>&1; then
  echo "ERROR: Test container already exists" >&2
  exit 1
fi

cleanup() {
  docker rm -f "$NAME" >/dev/null 2>&1 || true
}
trap cleanup EXIT

AUDIT_SQL="$(python3 - <<'PY'
import ast
from pathlib import Path

tree = ast.parse(Path("scripts/dev/bootstrap_migrations.py").read_text())
items = [
    ast.literal_eval(node.value)
    for node in tree.body
    if isinstance(node, ast.Assign)
    and any(isinstance(t, ast.Name) and t.id == "GRANTS_SQL"
            for t in node.targets)
]
assert len(items) == 1
print(items[0])
PY
)"

docker run -d --rm \
  --name "$NAME" \
  --network none \
  --tmpfs /var/lib/postgresql:rw,mode=1777 \
  -e POSTGRES_PASSWORD=throwaway_test_only \
  "$IMAGE" >/dev/null

ready=false
for i in {1..30}; do
  if docker exec "$NAME" pg_isready -U postgres -d postgres \
    >/dev/null 2>&1; then
    ready=true
    break
  fi
  sleep 1
done
[[ "$ready" == true ]] || { echo "PostgreSQL unavailable"; exit 1; }

psql_test() {
  docker exec -i "$NAME" psql -X -qAt \
    -v ON_ERROR_STOP=1 -U postgres -d postgres
}

sql() {
  docker exec "$NAME" psql -X -q \
    -v ON_ERROR_STOP=1 -U postgres -d postgres \
    -c "$1" >/dev/null
}

apply_grants() {
  psql_test < infra/postgres/reconcile-grants.sql >/dev/null
}

check_audit() {
  local expected="$1"
  local actual
  actual="$(printf '%s\n' "$AUDIT_SQL" | psql_test)"

  if [[ "$actual" != "$expected" ]]; then
    echo "FAIL: expected=$expected actual=$actual"
    exit 1
  fi
  echo "PASS: audit=$expected"
}

echo "=== Prepare schema ==="

psql_test <<'SQL' >/dev/null
CREATE ROLE transcendence_migrator NOLOGIN;
CREATE ROLE transcendence_app NOLOGIN;
GRANT USAGE, CREATE ON SCHEMA public TO transcendence_migrator;

SET ROLE transcendence_migrator;
CREATE TABLE public.users (id UUID PRIMARY KEY);
CREATE TABLE public.sessions (id UUID PRIMARY KEY);
CREATE TABLE public._prisma_migrations (id TEXT PRIMARY KEY);
RESET ROLE;
SQL

{
  echo 'SET ROLE transcendence_migrator;'
  cat backend/prisma/migrations/20261112140000_add_incidents/migration.sql
  echo 'RESET ROLE;'
} | psql_test >/dev/null

echo "=== Verify grants ==="
apply_grants
check_audit ready

echo "=== Verify missing and excessive privileges ==="

for change in \
  'REVOKE INSERT ON public.incident_events FROM transcendence_app' \
  'REVOKE USAGE ON SEQUENCE public.incident_events_seq_seq FROM transcendence_app' \
  'GRANT DELETE ON public.incidents TO transcendence_app' \
  'GRANT UPDATE ON public.incident_events TO transcendence_app' \
  'GRANT UPDATE ON SEQUENCE public.incident_events_seq_seq TO transcendence_app'
do
  sql "$change"
  check_audit pending
  apply_grants
  check_audit ready
done

echo "=== Verify ownership ==="

sql 'ALTER TABLE public.incident_events OWNER TO postgres'
check_audit pending

sql 'ALTER TABLE public.incident_events OWNER TO transcendence_migrator'
check_audit ready

echo "=== Verify idempotence ==="
apply_grants
check_audit ready

echo "PASS: Incident ACL integration regression"
