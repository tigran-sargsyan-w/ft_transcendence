# Architecture Principles and Module Boundaries

## Architecture direction

Prefer a modular NestJS application as the main backend, with clear module boundaries. Initial Nest modules include `auth`, `users`, and `realtime`. Additional Nest modules are introduced only when a real capability appears.

A **small set of separate services** is allowed when there is a clear reason (language fit, isolation, or ownership). Today that includes the Python Graph Engine (analysis-only, HTTP/JSON — see ADR 0001). Do not split into many services by default; keep inter-service communication simple.

## Module ownership

A module owns its internal implementation. Other modules must not reach into another module's private files, persistence implementation, or internal helpers.

Communication between modules should happen through an explicit public contract, a small interface, or an event when asynchronous decoupling is appropriate.

## Dependency rules

- Prefer dependencies on abstractions when multiple implementations or isolation from infrastructure is useful.
- Do not create interfaces only to mirror every class one-to-one.
- Keep framework-specific and infrastructure-specific details away from core business rules when practical.
- Avoid circular module dependencies.
- Prefer composition over inheritance unless inheritance models a genuine substitutable relationship.

## `shared/` rules

`shared/` is for code that is truly generic and reused by multiple modules. It must not become a dumping ground for domain-specific helpers.

Before moving code to `shared/`, ask:

1. Is it used by more than one module?
2. Is it independent of a specific module's business meaning?
3. Does sharing it reduce duplication without creating coupling?

If not, keep it inside the owning module.

## SOLID guidance

Use SOLID as design guidance, not as a requirement to create extra files.

- **SRP:** classes/functions/modules should have a clear reason to change.
- **OCP:** prefer extension points when multiple behaviors are expected to evolve.
- **LSP:** derived implementations must remain valid substitutes for their abstraction.
- **ISP:** keep contracts small and focused.
- **DIP:** high-level logic should not depend directly on replaceable infrastructure details.

## Evolution rule

Begin simple. Introduce deeper layers, strategies, factories, repositories, events, or adapters only when complexity justifies them. A complex module may later adopt internal `domain`, `application`, `infrastructure`, and `presentation` layers without forcing every module to do the same.
