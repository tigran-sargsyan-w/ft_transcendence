# ADR 0001: Python Graph Engine microservice

- Status: Accepted
- Date: 2026-09-14

## Context

Infrastructure topology analysis (blast radius, attack paths, critical nodes, risk scoring) needs a dedicated graph toolkit. The primary app stack is TypeScript (NestJS + React). Team agreement already favored Python for this capability.

## Decision

Use a **stateless Python microservice** (FastAPI + NetworkX) for graph analysis only.

- NestJS owns auth, persistence, discovery, and realtime; it sends topology snapshots and consumes results.
- Inter-service communication is plain HTTP/JSON only for now.
- The Graph Engine does not own the database or Docker discovery in early versions.
- Shared payload shapes live in `docs/architecture/graph-contract.md`.

## Consequences

- Clear ownership and a strong fit for graph algorithms (NetworkX).
- Extra runtime/image in Compose and a JSON contract to maintain.
- Supports evolving toward a microservices layout without splitting the whole backend early.
