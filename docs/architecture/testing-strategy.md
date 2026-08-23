# Testing Strategy

The testing approach is defined before selecting concrete tools. Framework-specific test runners and libraries are deferred until the stack is chosen.

## Test levels

### Unit tests

Use for isolated business rules, algorithms, validation logic, state transitions, and other deterministic behavior.

Business-critical logic should be testable without starting the full application or external infrastructure whenever practical.

### Module/integration tests

Verify that the public behavior of a module works with its important collaborators, including persistence adapters where appropriate.

Prefer testing a module through its public boundary instead of depending on private implementation details.

### API integration tests

Verify HTTP contracts such as authentication, validation, status codes, authorization, and response/error formats.

### End-to-end tests

Cover a small number of critical user journeys through the deployed/runnable application. Do not attempt to replace all lower-level tests with E2E tests.

## Priorities

Prioritize tests for:

- authentication and authorization
- concurrency-sensitive behavior
- real-time state transitions
- algorithms/scoring/matchmaking when introduced
- security-sensitive validation
- data consistency and invariants

## Testability rule

If important logic can only be tested through a browser or by running the complete infrastructure, consider whether the design is too tightly coupled.

## Tooling

The specific unit, integration, E2E, mocking, database-test, and coverage tools remain undecided until the frontend/backend stack is selected.
