# ft_transcendence

Infrastructure Intelligence / Real-Time Incident Platform built for the 42 `ft_transcendence` project.

The first implementation slice focuses on one end-to-end path: a browser client connects to the backend, checks API health, receives live infrastructure events, and renders them in an operations dashboard.

## Bootstrap stack (proposed)

- Frontend: React + TypeScript + Vite
- Backend: NestJS + TypeScript
- Real-time transport: Socket.IO / WebSockets
- Database: PostgreSQL
- Local orchestration: Docker Compose

The stack is introduced through a feature branch/PR so the team can review it before it becomes a permanent architectural decision.

## Run locally

```bash
cp .env.example .env
docker compose up --build
```

Then open `http://localhost:5173`.

Backend health endpoint: `http://localhost:3000/api/v1/health`.

## Current vertical slice

1. Frontend starts.
2. Backend starts and exposes `/api/v1/health`.
3. Frontend connects to the realtime namespace.
4. Backend sends an infrastructure snapshot and recurring telemetry ticks.
5. Dashboard renders service state and a live event feed.

This is intentionally small: the goal is to get the project running end-to-end before adding authentication, persistence, incident workflows, security infrastructure, and observability modules.
