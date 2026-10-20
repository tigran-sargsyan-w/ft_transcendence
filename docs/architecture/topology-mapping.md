# Topology Mapping

How the Nest `topology` module (`backend/src/modules/topology/`) turns what the [Collector](./collector-contract.md) reports into the nodes and edges of the [Graph contract](./graph-contract.md).

## Principle

Nest keeps the containers the collector reports (one snapshot, then events) and derives the whole graph from them. A delta is only the difference between two whole graphs, so a snapshot never holds an edge without its two nodes, and an event with no effect produces no delta.

Only containers carry information. A network or a volume is a node because a container uses it; network and volume events add nothing, as containers already list their networks and mounts.

## Nodes

| Kind | Id | Label | Status | Exists when |
|------|----|-------|--------|-------------|
| `service` | `svc:<project>:<service>` for a Compose service, `ctr:<name>` for any other container | Compose service name, or container name | from the containers, see below | a container of that service exists (one node whatever the number of instances) |
| `network` | `net:<name>` | network name | `healthy` | a container is attached to it |
| `volume` | `vol:<name>` | volume name | `healthy` | a container mounts the named volume |
| `host` | `host:<environmentId>` | `Docker host` | `healthy` | there is at least one service |
| `external` | `ext:internet` | `Internet` | `unknown` | a service publishes a port beyond loopback |

A service id comes from the Compose project and service, never from the container id: it stays the same when Compose recreates the container.

`metadata` of a service: `image`, `composeProject`, `composeService` (Compose only) and `ports` (`containerPort`, `protocol`, `hostPort`, `loopbackOnly`).

## Edges

An edge leaves the one that depends and points to its dependency (agreed on 23/09).

| Kind | From → to | Rule |
|------|-----------|------|
| `depends_on` | service → service | Compose `depends_on`, same project, target exists |
| `connects_to` | service → network | the container lists the network |
| `mounts` | service → volume | named volume; `metadata.readOnly` is true only if every mount is read-only |
| `runs_on` | service → host | every service |
| `exposes` | `ext:internet` → service | a published port not bound to loopback only; `metadata.ports` lists them |

An edge id is `<kind>:<source>><target>`, so there is at most one edge per kind and pair. The same port bound on `0.0.0.0` and on `::` counts once.

## Status of a service

| Docker state | Health | Status |
|--------------|--------|--------|
| `running` | `healthy` or none | `healthy` |
| `running` | `unhealthy` | `degraded` |
| `restarting` | any | `degraded` |
| `running` | `starting` | `unknown` |
| `created` | any | `unknown` |
| `exited`, `dead`, `paused`, `removing` | any | `down` |
| any other | any | `unknown` |

`compromised` is never produced from the collector. `unknown` means information is missing (not started yet, healthcheck not settled), not that the service failed.

With several instances (recreation, scaling) the service shows its best status: `healthy`, then `degraded`, `unknown`, `down`.

## Events

| Event | Effect |
|-------|--------|
| `container.created`, `started`, `stopped`, `health_changed` | the container object replaces the one stored |
| `container.died` with a container | same |
| `container.died` without a container | nothing: the last known state is kept, never read as `down` |
| `container.destroyed` | the container is removed |
| network and volume events, unknown types | nothing; the sequence still advances |

## Revision and resynchronization

- `epoch` names the in-memory state: a restart of Nest gets another one.
- `revision` counts the batches applied (a batch with no effect spends none) and never goes down. A resynchronization is a new snapshot with the next revision.
- Both are Nest's own: neither follows the collector's `streamId` nor its `sequence`.
- The collector's position is the pair (`streamId`, `sequence`). A batch is applied only if it is on the same stream and continues the sequence; events already applied are dropped. Otherwise Nest loads a new snapshot: a different `streamId` (even with a lower or higher sequence), a gap, or no snapshot yet. The state stays as it was until then, so nothing wrong is published.

## Sending the topology to the Graph Engine

`toEngineTopology(snapshot)` (`backend/src/modules/topology/engine-topology.ts`) turns the live snapshot into the `topology` of a `POST /analyze` request: `schemaVersion`, `environmentId`, `capturedAt`, `topologyRevision`, `nodes` and `edges`, nothing else.

`topologyRevision` is `<epoch>:<revision>` (`topologyRevisionOf`). The engine echoes it unchanged, so Nest compares it with the current value and takes a different one for a stale result. The epoch comes first because a restart of Nest starts the revision over at 1.

Do not send the live snapshot as it is. The engine ignores the fields it does not know (`epoch`, `revision`) without an error, then echoes no `topologyRevision`, and the stale check is off without anyone noticing. A missing echo means "cannot be checked", never "up to date". A revision sent as an integer is refused (HTTP 400).

For an analysis that starts from the outside (`ext:internet`), note that this node only exists when a service publishes a port beyond loopback.

## Known limits (v0)

- Bind mounts have no node: the collector redacts their source, so they have no identity.
- A crashed replica is hidden while another instance of the service runs.
- `network.connected` and `network.disconnected` have no effect: `connects_to` follows the container's list of networks, which still holds the network after a stop.
- A `container.died` without container about a container Nest never saw is ignored: its project and service are unknown.
- An external node only exists for published ports; nothing else is known about the outside.
- Ids use `:` and `>` as separators. Compose project and service names cannot contain `:` (Compose specification); the names of standalone containers, networks and volumes are used as Docker gives them.
