# Backend End-to-End Testing

Backend E2E tests cover authentication, sessions, user profiles, incidents,
analyses, rate limiting, and authenticated Socket.IO flows.

## GitHub Actions

The `backend-e2e` job in `.github/workflows/ci.yml` runs on pull requests
targeting `main` and on pushes to `main`.

The job:

1. Starts an isolated PostgreSQL 18 service.
2. Installs backend dependencies using `npm ci`.
3. Generates the Prisma Client.
4. Applies all Prisma migrations with `prisma migrate deploy`.
5. Executes all backend E2E suites with `npm run test:e2e -- --no-file-parallelism`.

The CI database uses test-only credentials. It does not require Vault, the
development PostgreSQL instance, or production secrets. A failing E2E suite
fails the CI job.

## Local execution

Run the following from the **repository root** in a shell with Docker and Node.js
installed. The script starts a disposable PostgreSQL 18 container with temporary
storage and a random loopback port. It does **not** access the project's existing
database, Vault, or persistent Docker volumes.

```bash
bash <<'RUN_BACKEND_E2E'
set -Eeuo pipefail

NAME="ft-backend-e2e-$$"

cleanup() {
  docker rm -f "$NAME" >/dev/null 2>&1 || true
}
trap cleanup EXIT

docker run --rm -d \
  --name "$NAME" \
  --network bridge \
  --publish 127.0.0.1::5432 \
  --tmpfs /var/lib/postgresql:rw,mode=1777 \
  -e POSTGRES_USER=transcendence_e2e \
  -e POSTGRES_PASSWORD=e2e_test_only \
  -e POSTGRES_DB=transcendence_e2e \
  postgres:18-bookworm >/dev/null

READY=false
for i in {1..40}; do
  if docker exec "$NAME" pg_isready \
    -U transcendence_e2e \
    -d transcendence_e2e >/dev/null 2>&1; then
    READY=true
    break
  fi
  sleep 1
done

if [[ "$READY" != true ]]; then
  echo "PostgreSQL not ready" >&2
  exit 1
fi

ADDRESS="$(docker port "$NAME" 5432/tcp)"
if [[ "$ADDRESS" =~ ^127\.0\.0\.1:([0-9]+)$ ]]; then
  PORT="${BASH_REMATCH[1]}"
else
  echo "Unexpected PostgreSQL port binding: $ADDRESS" >&2
  exit 1
fi

(
  cd backend

  unset DATABASE_URL_FILE
  export DOTENV_CONFIG_PATH=/dev/null
  export DATABASE_URL="postgresql://transcendence_e2e:e2e_test_only@127.0.0.1:${PORT}/transcendence_e2e?schema=public"

  npm ci
  npm run prisma:generate
  ./node_modules/.bin/prisma migrate deploy
  npm run test:e2e -- --no-file-parallelism
)

echo "All backend E2E tests passed"
RUN_BACKEND_E2E
```

The temporary PostgreSQL container is removed automatically after the run,
including when a command fails. Each invocation uses a fresh database.

## Troubleshooting

- **Docker unavailable:** check the Docker daemon and current user's access.
- **PostgreSQL not ready:** inspect the disposable container's startup output.
- **Migration failure:** inspect Prisma's migration output and repository history.
- **E2E test failure:** inspect the failed Vitest suite and its logs.
- **CI-only failure:** compare Node.js, PostgreSQL, and environment settings
  with the GitHub runner.

Do not substitute development or production database credentials for the
isolated test credentials.

## Scope and limitations

This job tests backend HTTP/Socket.IO integration against an isolated database.
It is **not** a substitute for tests of the complete Vault-backed startup,
Nginx/ModSecurity, or the full deployed application. The CI job uses a test
PostgreSQL user with database permissions sufficient for the existing suites;
the restricted `transcendence_app` ACL regression is a separate infrastructure
test.
