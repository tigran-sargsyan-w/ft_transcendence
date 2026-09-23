# Collector Contract

**Status: draft v0**, written from the Nest side (the consumer). To be validated by Tigran (collector); the mapping to the graph is to be reviewed by Camille.

The Docker collector serves the current state of a Docker/Compose environment (a **snapshot**) and a numbered stream of **events** over internal HTTP/JSON. Nest reads both to build the topology (`nodes` / `edges`, see [Graph contract](./graph-contract.md)). Nothing else calls the collector.

| Point | Status |
|-------|--------|
| HTTP/JSON transport | Decided (Tigran) |
| Docker reached through a read-only socket proxy, no direct `docker.sock` mount | Decided (Tigran) |
| Nest reads from the collector (snapshot + long polling) | **Proposal**, to validate with Tigran |

## Roles

- **Collector:** talks to Docker, normalizes, numbers the events, keeps a short in-memory buffer. It reports what Docker says: it builds no nodes or edges and analyzes nothing. One collector serves one environment.
- **Nest:** never talks to Docker. Turns snapshot + events into `nodes` / `edges` and keeps the live topology.
- **Not in v0:** authentication between services, several environments, sources other than Docker/Compose, persistence of the event buffer.

## Conventions

Same as the [Graph contract](./graph-contract.md): `schemaVersion` (integer, starts at `1`), `environmentId` (opaque, e.g. `env_local_compose`), opaque string ids, ISO 8601 UTC dates, `camelCase` fields, `{ "data": … }` on success and `{ "error": { "code", "message" } }` on failure ([API conventions](./api-conventions.md)). Ids here are Docker's own; the stable graph ids (`svc_api`…) are built by Nest.

## Snapshot

The full state at one moment: `{ schemaVersion, environmentId, capturedAt, sequence, containers, networks, volumes }`.

`sequence` is the last event the snapshot includes: it reflects every event up to that number and none after. Events carry whole objects (see [Events](#events)), so applying an event the snapshot already reflects is harmless.

**Container**

| Field | Notes |
|-------|-------|
| `id` | Docker container id. Identifies an **instance**: it changes when Compose recreates the container |
| `name` | Without the leading `/` |
| `image` | As configured (`busybox:1.37`) |
| `state` | Docker's state: `created` `running` `paused` `restarting` `removing` `exited` `dead` |
| `health` | `healthy` `unhealthy` `starting` `none` (no healthcheck, or not started). Only meaningful while `running`: a stopped container was seen `unhealthy` |
| `labels` | All labels except `com.docker.compose.*` (same rule for networks and volumes) |
| `compose` | `{ project, service, dependsOn }` from the Compose labels, `null` if not managed by Compose |
| `ports` | Published ports `{ containerPort, protocol, hostIp, hostPort }`, read from the port bindings (no IPv4/IPv6 duplicate). `hostIp` is `0.0.0.0` when Docker leaves it empty |
| `networks` | `{ networkId, name, ipv4Address }`. `networkId` and `ipv4Address` are empty while the container is not attached (just created, or exited) |
| `mounts` | `{ type, source, destination, readOnly }`. `source` is the volume **name** for a volume, the host path for a bind mount |

**Network:** `id`, `name`, `driver`, `internal`, `labels`. **Volume:** `name` (Docker volumes have no id), `driver`, `labels`.

Environment variables and command lines are never forwarded: they routinely contain secrets.
