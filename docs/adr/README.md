# Architecture Decision Records (ADR)

ADRs preserve important technical decisions and their context so the team can later explain not only what was built, but why it was built that way.

## When to create an ADR

Create an ADR when a decision is significant, difficult to reverse, affects multiple parts of the project, or has meaningful trade-offs.

Typical examples:

- frontend/backend framework selection
- database and ORM selection
- authentication/session approach
- modular monolith vs microservices
- WebSocket/realtime technology
- event-driven communication decisions
- major security/infrastructure choices

Do not create an ADR for trivial implementation details.

## Naming

Use sequential numbering:

```text
0001-short-decision-title.md
0002-another-decision.md
```

## Status

Use one of:

- `Proposed`
- `Accepted`
- `Superseded`
- `Rejected`

If a decision replaces an earlier ADR, link both records rather than rewriting history.

## Process

1. Copy `template.md`.
2. Describe the context/problem.
3. Record the considered options and important trade-offs.
4. State the decision.
5. Record consequences.
6. Review through the normal pull-request process.

ADRs should stay concise and understandable to every team member.
