# Nest ↔ Graph Engine smoke

Manual checks that the live analyses path works end to end:

Compose → topology → Nest `POST /api/v1/environments/{id}/analyses` → Graph Engine → results + freshness.

## Prerequisites

- Root `.env` present (copy from `.env.example` if needed).
- Compose services up: at least `postgres`, `backend`, `collector`, `docker-proxy`, `graph-engine`.
- Environment id: `env_local_compose` (`ENVIRONMENT_ID` on the collector).
- Nest reaches the engine via `GRAPH_ENGINE_URL` (`http://graph-engine:8000` in Compose).
- Host ports for debugging: Nest `127.0.0.1:3000`, Graph Engine `127.0.0.1:8000`, collector `127.0.0.1:3001`.

## Health checks

```bash
docker compose up -d --build postgres backend collector docker-proxy graph-engine

curl -sS http://127.0.0.1:8000/api/v1/health
curl -sS http://127.0.0.1:3000/api/v1/health
```

Both should return a success envelope (not connection refused). If Nest health fails, check `postgres` and backend logs before going further.

## Session (required for analyses)

Analyses need a session cookie (`sid`). Against plain `http://127.0.0.1:3000`, set `COOKIE_SECURE=false` on the backend (Compose does not pass it today; default is secure-only).

```bash
# once per machine / when the user does not exist yet
curl -sS -X POST http://127.0.0.1:3000/api/v1/auth/register \
  -H 'Content-Type: application/json' \
  -d '{"email":"smoke@example.com","password":"smokepass1"}'

curl -sS -c /tmp/iip-smoke.cookies -X POST http://127.0.0.1:3000/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"smoke@example.com","password":"smokepass1"}'

curl -sS -b /tmp/iip-smoke.cookies http://127.0.0.1:3000/api/v1/auth/me
```

`/auth/me` should return the user. If login succeeds but `/auth/me` is `401`, the cookie was not sent — almost always `COOKIE_SECURE`.
