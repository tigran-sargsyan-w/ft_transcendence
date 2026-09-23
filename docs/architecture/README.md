# Architecture

This directory holds shared architecture rules, API/realtime conventions, and links to ADRs.

Documents here should stay short and stable. Concrete stack and product decisions are recorded under **Current status** and in ADRs when they become firm.

## Documents

- [Principles and module boundaries](./principles.md)
- [Authentication and users](./auth-and-users.md)
- [API conventions](./api-conventions.md)
- [Graph contract](./graph-contract.md) (topology snapshot and analyze API v0)
- [Collector contract](./collector-contract.md) (Docker collector snapshot and events, draft v0)
- [Security baseline](./security-baseline.md)
- [Testing strategy](./testing-strategy.md)
- [Real-time conventions](./realtime-conventions.md)
- [Architecture Decision Records](../adr/README.md)

## Current status

**Decided** (see code and/or ADRs):

- final product/domain concept — Infrastructure Intelligence / real-time incident platform
- frontend framework — React + Vite (TypeScript)
- backend framework — NestJS (TypeScript) as the main app API, with a **small set of services** (e.g. Docker collector, Python Graph Engine) rather than one single process for everything
- database/ORM — PostgreSQL + Prisma
- Graph Engine (product-specific) — Python FastAPI + NetworkX, plain HTTP/JSON — [ADR 0001](../adr/0001-python-graph-engine.md), [graph contract](./graph-contract.md)

**Still open:**

- concrete authentication/session mechanism
- concrete WebSocket library
- product-specific modules and events (beyond the graph contract v0)

These documents should evolve through pull requests and ADRs when a decision becomes concrete.
