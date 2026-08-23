# Real-Time Conventions

This document defines a product-agnostic live communication model. It does not select a WebSocket library or commit the final product to a particular room/match/scenario model.

## Connection

- A live connection must have a clear lifecycle: connect, authenticate when required, active, disconnect, reconnect.
- The server remains authoritative for protected/shared state.
- Connection identity must not be accepted solely from client-provided user IDs.

## Channels / rooms

Use a generic server-managed grouping concept for broadcasting updates to a subset of clients. The final product may interpret a group as a room, match, scenario, workspace, session, or another domain concept.

Clients must not gain access to a protected group merely by guessing its identifier.

## Event naming

Prefer stable names that describe what happened or what is requested, for example:

```text
room.join
room.joined
session.updated
something.failed
```

Product-specific names are deferred until the domain is selected.

## Event envelope

A consistent envelope is preferred:

```json
{
  "event": "session.updated",
  "payload": {},
  "timestamp": "2026-01-01T00:00:00Z"
}
```

Optional fields such as correlation/request IDs may be added when acknowledgement and tracing requirements become concrete.

## Acknowledgements and errors

Commands that require confirmation should have a defined success/failure response rather than relying only on optimistic client state. Errors must use stable machine-readable codes and must not expose sensitive server internals.

## Disconnect and reconnect

The design must distinguish between:

- permanent leave
- temporary transport loss
- server-side removal/authorization failure

When product state requires it, reconnecting clients should be able to recover authoritative state instead of assuming their local state is current.

## Synchronization rule

Do not make every client an equal authority for shared state. The concrete synchronization model will be chosen per feature, but conflict/race handling must be explicit for concurrent actions.

## Deferred decisions

- WebSocket library/protocol implementation
- persistence of live state
- distributed pub/sub
- reconnect grace periods
- ordering guarantees
- product-specific event catalogue
