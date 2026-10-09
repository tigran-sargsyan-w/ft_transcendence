# Nginx Reverse Proxy — Development Routing

## Overview

Nginx is the application's browser-facing entry point. It routes frontend requests to Vite and API requests to NestJS while preserving the original request paths.

## Routing

| Browser path | Upstream | Purpose |
|---|---|---|
| `/` and frontend assets | `frontend:5173` | React/Vite application |
| `/api/*` | `backend:3000` | NestJS REST API |
| `/socket.io/*` | `backend:3000` | Reserved for Socket.IO integration |
| Vite HMR WebSocket | `frontend:5173` | Development hot module replacement |

All upstream names are resolved through the Docker Compose network.

## Development Ports

- `localhost:8080` — Nginx application entry point.
- `localhost:5173` — Direct Vite access for debugging.
- `localhost:3000` — Direct NestJS API access for debugging.
- `localhost:3001` — Direct Collector access for debugging.
- `localhost:2375` — Read-only Docker proxy for local debugging.

The application uses Nginx as its normal browser entry point. Direct service ports are retained temporarily for development and are not the intended production exposure model.

## Verification

```bash
docker compose config --quiet
docker compose up -d nginx
docker compose exec nginx nginx -t

curl -i http://127.0.0.1:8080/
curl -i http://127.0.0.1:8080/api/v1/health
curl -i http://127.0.0.1:8080/api/v1/nonexistent
```

Expected results:

- Frontend HTML: HTTP 200.
- Backend health: HTTP 200.
- Unknown API route: HTTP 404 with the NestJS JSON error envelope.
- Vite HMR WebSocket: HTTP 101 Switching Protocols.

## Future Integration

- **HTTPS:** Introduce TLS termination at the Nginx boundary.
- **Socket.IO:** Validate WebSocket upgrade, HTTP long-polling and reconnect behavior.
- **ModSecurity:** Integrate the WAF and OWASP CRS without bypassing application routing.
- **Production:** Remove direct application service port exposure and apply production network hardening.

These future features are outside the scope of issue #42.