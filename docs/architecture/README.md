# Architecture

This directory contains the framework-agnostic architecture foundation for the project.

The goal of Architecture v0 is to define stable engineering rules before the final product concept and technology stack are selected. It intentionally avoids premature implementation details.

## Documents

- [Principles and module boundaries](./principles.md)
- [Authentication and users](./auth-and-users.md)
- [API conventions](./api-conventions.md)
- [Security baseline](./security-baseline.md)
- [Testing strategy](./testing-strategy.md)
- [Real-time conventions](./realtime-conventions.md)
- [Architecture Decision Records](../adr/README.md)

## Current status

The following remain intentionally undecided:

- final product/domain concept
- frontend framework
- backend framework
- database/ORM
- concrete authentication/session mechanism
- concrete WebSocket library
- product-specific modules and events

These documents should evolve through pull requests and ADRs when a decision becomes concrete.
