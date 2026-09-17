# Architecture

This directory contains the framework-agnostic architecture foundation for the project.

The goal of Architecture v0 is to define stable engineering rules before the final product concept and technology stack are selected. It intentionally avoids premature implementation details.

## Documents

- [Principles and module boundaries](./principles.md)
- [Authentication and users](./auth-and-users.md)
- [API conventions](./api-conventions.md)
- [Graph contract](./graph-contract.md) (topology snapshot and analyze API v0)
- [Security baseline](./security-baseline.md)
- [Testing strategy](./testing-strategy.md)
- [Real-time conventions](./realtime-conventions.md)
- [Architecture Decision Records](../adr/README.md)

## Current status

**Decided** (see code and/or ADRs):

- final product/domain concept — Infrastructure Intelligence / real-time incident platform
- frontend framework — React + Vite (TypeScript)
- backend framework — NestJS (TypeScript), modular monolith first
- database/ORM — PostgreSQL + Prisma
- Graph Engine (product-specific) — Python FastAPI + NetworkX, plain HTTP/JSON — [ADR 0001](../adr/0001-python-graph-engine.md), [graph contract](./graph-contract.md)

**Still open:**

- concrete authentication/session mechanism
- concrete WebSocket library
- product-specific modules and events (beyond the graph contract v0)

These documents should evolve through pull requests and ADRs when a decision becomes concrete.
