# ADR 0001: Bootstrap technology stack

- Status: Proposed
- Date: 2026-08-28

## Context

The product direction is now concrete enough to stop treating all technology choices as hypothetical. The project needs a frontend, backend, database, concurrent multi-user behavior, realtime communication, and containerized local/deployed execution. The team also wants an architecture suitable for infrastructure/security visualization and event-driven features.

The immediate goal is not to lock every future infrastructure choice. It is to establish a small, runnable end-to-end slice that the team can review through a pull request.

## Considered options

### React + NestJS + PostgreSQL

One TypeScript language across frontend and backend, strong modular backend structure, direct WebSocket support, mature ecosystem, and a relational database suited to users/incidents/audit history.

### React + Fastify + PostgreSQL

Smaller backend surface and high performance, but more application structure and conventions would need to be designed by the team.

### React + Django/FastAPI + PostgreSQL

Strong backend choices, especially for future analytics/data work, but introduces a second primary language and additional cross-stack context switching for the initial slice.

## Decision

Propose the following bootstrap stack for implementation review:

- React + TypeScript + Vite
- NestJS + TypeScript
- Socket.IO/WebSockets for the first realtime transport
- PostgreSQL as the relational database
- Docker Compose for local orchestration

This ADR remains **Proposed** until the team reviews the implementation PR. Additional choices such as ORM, caching, event broker, WAF, Vault, Prometheus/Grafana, and log pipeline remain separate decisions.

## Consequences

### Positive

- The team can start coding immediately.
- Shared TypeScript contracts can be introduced later without cross-language serialization friction.
- NestJS naturally supports the existing modular architecture direction.
- The stack can satisfy the frontend/backend framework and realtime needs of the subject.
- PostgreSQL provides a stable base for structured product state and history.

### Negative / trade-offs

- NestJS has more framework ceremony than Fastify alone.
- Socket.IO adds a protocol layer over raw WebSockets.
- A TypeScript-first backend is less natural than Python for some future data-science workloads; such workloads can still become isolated services if justified.

## Related

- `docs/architecture/principles.md`
- `docs/architecture/realtime-conventions.md`
- Bootstrap implementation PR created from `feat/bootstrap-platform`.
