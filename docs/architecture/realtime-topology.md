# Realtime Topology Protocol

How the browser follows the live infrastructure graph through Socket.IO. The envelope and the acknowledgement rules come from [Real-time conventions](./realtime-conventions.md), the node and edge shapes from the [Graph contract](./graph-contract.md), and the way the graph is built from Docker from [Topology mapping](./topology-mapping.md).

Nest sends a full snapshot, then small deltas. Analyses are not part of this protocol: they go over HTTP (see the Graph contract).

## Status

| Part | State |
|------|-------|
| Snapshot, delta, `epoch` and `revision` | Implemented in the `topology` module |
| Socket.IO gateway: handshake, rooms, commands | Not implemented yet |
| `topology:status` | Specified here only: the module does not report collector outages yet, so the gateway has nothing to send |

## Connection

- Path `/socket.io/` (the Socket.IO default) on the same origin as the API: Nginx routes it to the backend ([Nginx routing](./nginx-routing.md)). Both transports must work, WebSocket and HTTP long polling.
- Identity comes from the session cookie `sid` ([Authentication and users](./auth-and-users.md)), checked on the handshake request. The browser attaches the cookie by itself; the client sends no token and no user id.
- A refused handshake raises `connect_error` on the client, with `error.data = { code, message }`:

| Code | When |
|------|------|
| `UNAUTHORIZED` | no `sid` cookie, or the session is unknown or expired |
| `FORBIDDEN` | the `Origin` header is not the application's |

- After a refusal Socket.IO does not reconnect: `socket.active` is `false` in the `connect_error` handler, and `socket.connect()` would have to be called by hand ([Socket.IO docs](https://socket.io/docs/v4/client-socket-instance/#connect_error)). On `UNAUTHORIZED` the client sends the user to the login page. A network failure also raises `connect_error`, but the socket retries by itself (`socket.active` is `true`).

## Reconnection

Socket.IO reconnects by itself after a lost connection. The new socket has a new id and is in no room: sockets leave their rooms on disconnection, and connection state recovery is off by default ([rooms](https://socket.io/docs/v4/rooms/#disconnection), [connection state recovery](https://socket.io/docs/v4/connection-state-recovery#usage)).

- The client sends `topology:subscribe` on every `connect` event, not only the first, and receives a snapshot.
- It emits commands only while `socket.connected` is `true`. Otherwise Socket.IO buffers them and sends them on reconnection, whatever the state of the client by then ([offline behavior](https://socket.io/docs/v4/client-offline-behavior/#buffered-events)).
- If the session expired meanwhile, the new handshake is refused with `UNAUTHORIZED`.

## Messages

**Server to client**: the envelope of the real-time conventions. `event` repeats the Socket.IO event name, `timestamp` is the moment the server sent the message (ISO 8601 UTC).

**Client to server**: commands. The argument is a plain object, with no envelope. The server answers every command through the Socket.IO acknowledgement, in the shape of the HTTP API ([API conventions](./api-conventions.md)):

```json
{ "data": { "environmentId": "env_local_compose" } }
```

```json
{ "error": { "code": "VALIDATION_ERROR", "message": "Request validation failed", "details": { "environmentId": ["environmentId must be a string"] } } }
```

Arguments are validated on the server, and a property a command does not accept is refused, as over HTTP.

## Rooms

One room per environment: `env:<environmentId>`. A client never names a room: it subscribes, and the server decides. A socket follows one environment at a time, because a delta does not name its environment.

There is one environment for now. Its id comes from the collector (`ENVIRONMENT_ID`, `env_local_compose` in Compose) and no endpoint gives it to the front, so `environmentId` is optional in `topology:subscribe`.

Every authenticated user may subscribe: there is no permission per environment yet. The rooms `incident:<incidentId>` and `user:<userId>`, and the delivery of analysis results, are not specified yet.

## Client to server

| Event | Argument | Effect | Acknowledgement |
|-------|----------|--------|-----------------|
| `topology:subscribe` | `{ environmentId? }` | Joins the room, then sends this socket the snapshot and the status. Subscribing again sends them again. | `{ data: { environmentId } }` |
| `topology:unsubscribe` | none | Leaves the room. Nothing happens if the socket is not subscribed. | `{ data: {} }` |
| `topology:resync` | none | Sends this socket, and only this socket, the snapshot and the status. | `{ data: { environmentId } }` |

`environmentId`, when present, is a non-empty string. Absent means the environment the server follows; any other value is refused with `TOPOLOGY_UNKNOWN_ENVIRONMENT`.

For `subscribe` and `resync`, the acknowledgement comes first, then `topology:snapshot`, then `topology:status`. Socket.IO keeps the order of messages on one connection ([delivery guarantees](https://socket.io/docs/v4/delivery-guarantees#message-ordering)).

## Server to client

| Event | Payload | Sent |
|-------|---------|------|
| `topology:snapshot` | the whole topology | to a socket after `subscribe` or `resync`; to the whole room when Nest loads a new one |
| `topology:delta` | the changes of one revision | to the room, after each change |
| `topology:status` | `{ stale, since }` | to a socket after its snapshot; to the room when `stale` changes |

A socket that subscribes gets a snapshot of revision `R`, then every delta after it, the first one with `baseRevision` `R`: none falls between.

### `topology:snapshot`

| Field | Notes |
|-------|-------|
| `schemaVersion` | `1` |
| `environmentId` | |
| `capturedAt` | ISO 8601 UTC. The latest change the graph reflects: the later of the collector's snapshot and the events applied since. Not a freshness clock: a quiet infrastructure keeps an old `capturedAt`. `topology:status` tells whether the graph may be out of date. |
| `epoch` | See [Revision and epoch](#revision-and-epoch) |
| `revision` | Integer, at least `1`: `1` for the first snapshot of an `epoch`, higher after that |
| `nodes`, `edges` | The whole graph, sorted by id, in the shapes of the Graph contract |

This is not the topology Nest sends to the Graph Engine: there `epoch` and `revision` become the single string `topologyRevision` ([Topology mapping](./topology-mapping.md#sending-the-topology-to-the-graph-engine)).

### `topology:delta`

`{ epoch, baseRevision, revision, operations }`, with `revision` equal to `baseRevision + 1`. An operation is one of:

| `op` | Fields | Effect |
|------|--------|--------|
| `edge.remove` | `id` | removes the edge |
| `node.remove` | `id` | removes the node |
| `node.upsert` | `node` | adds the node, or replaces the one with the same id |
| `edge.upsert` | `edge` | adds the edge, or replaces the one with the same id |

The operations come in this order: edge removals, node removals, node upserts, edge upserts. Applied in order, an edge never points to a node that is not there.

A delta is never empty: a change with no effect produces no delta and no revision. A burst of Docker events (a `docker compose up`) comes out as a few deltas, because the server waits 300 ms after each one before reading on. Do not expect one delta per action.

### `topology:status`

`{ stale: boolean, since: string | null }`. `stale: true` means the collector has not answered for a while: the graph on screen is the last known one and may be out of date. `since` is when the outage began (ISO 8601 UTC), `null` when `stale` is `false`. See [Collector outages](#collector-outages).

### TypeScript sketch (informative)

`GraphNode` and `GraphEdge` are those of the Graph contract.

```ts
type TopologySnapshot = {
  schemaVersion: 1;
  environmentId: string;
  capturedAt: string;
  epoch: string;
  revision: number;
  nodes: GraphNode[];
  edges: GraphEdge[];
};

type TopologyOperation =
  | { op: 'node.upsert'; node: GraphNode }
  | { op: 'node.remove'; id: string }
  | { op: 'edge.upsert'; edge: GraphEdge }
  | { op: 'edge.remove'; id: string };

type TopologyDelta = {
  epoch: string;
  baseRevision: number;
  revision: number;
  operations: TopologyOperation[];
};

type TopologyStatus = { stale: boolean; since: string | null };
```

## Revision and epoch

The server keeps two values, both its own: they follow neither the `streamId` nor the `sequence` of the collector.

- `epoch` is a UUID that names one life of Nest. A restart gives another one.
- `revision` starts at `1` with the first snapshot. It goes up by one for each batch that changes the graph and each time a snapshot is loaded. It never goes down.
- A reset of the collector's stream does not change the `epoch`: Nest rebuilds the topology and publishes a snapshot with the next revision.

The client keeps the `epoch` and the `revision` of the last message it applied:

```text
connect            emit topology:subscribe
topology:snapshot  replace the whole graph, remember (epoch, revision), compare nothing
topology:delta     if epoch and baseRevision equal the remembered ones:
                     apply the operations in order, remember the new revision
                   otherwise ignore it and emit topology:resync
                   (at most one resync until the next snapshot)
topology:status    show or hide the "may be out of date" notice, keep the graph
```

A snapshot is always adopted as it is, even when its revision is lower than the client's: a different `epoch` means Nest restarted and the revision started over at `1`, so the two numbers cannot be compared.

A delta that does not follow means a change was missed. Over one connection Socket.IO delivers in order, and it can lose a message when the connection drops, after which the client subscribes again. The rule is a safety net.

## Collector outages

While the collector cannot be reached, Nest publishes nothing and keeps the last graph. It tries again after 1 s, then doubles the pause up to 30 s.

- After 10 s without an answer, the server sends the room `topology:status { stale: true, since }`. A shorter break sends nothing, so a blip does not make the interface flicker.
- The graph stays on screen with a notice. The last graph is the best information there is; only its age is uncertain.
- When the collector answers, Nest loads a new snapshot (revision +1, same `epoch`) and sends the room `topology:snapshot`, then `topology:status { stale: false, since: null }`. The status comes second so the notice never disappears while the old graph is still on screen.
- This can take up to 30 s after the collector is back, the longest pause between tries. The client sees a jump in revision, not a series of deltas.
- A new snapshot is loaded the same way after a reset of the collector's stream, a gap in its events, or an expired sequence. A snapshot can arrive at any time, even several in a row: the client replaces its graph each time.
- `stale` is about an existing graph. If Nest has never reached the collector, or `COLLECTOR_URL` is not set, there is no graph and `topology:subscribe` is refused with `TOPOLOGY_UNAVAILABLE`.

## Reading the graph

- `unknown` means information is missing (container created, healthcheck still starting, Docker state not recognised), not that the service failed. Do not show it as an alert. `ext:internet` is always `unknown`: nothing is known about the outside.
- `compromised` is never produced by the live topology.
- Ids are opaque and stable: a service keeps its id when Compose recreates its container. Their shapes help to read logs: `svc:<project>:<service>`, `ctr:<name>` (a container outside Compose), `net:<name>`, `vol:<name>`, `host:<environmentId>`, `ext:internet`, and `<kind>:<source>><target>` for an edge.
- `ext:internet` exists only while a service publishes a port beyond loopback. A stopped container publishes no port, so its `exposes` edge, and the node when nothing else is exposed, disappear while it is stopped.
- The environment is the whole Docker host: containers that are not part of the project appear too.
- A service with several instances shows its best status.

## Errors

Handshake errors are in [Connection](#connection). The acknowledgement of a command can carry the codes below, all listed in the [API conventions](./api-conventions.md#error-codes):

| Code | When |
|------|------|
| `VALIDATION_ERROR` | Invalid argument, or a property the command does not accept; `details` as in the API conventions |
| `TOO_MANY_REQUESTS` | More than 10 commands in 10 s on one connection |
| `TOPOLOGY_UNKNOWN_ENVIRONMENT` | `environmentId` is not the one the server follows |
| `TOPOLOGY_UNAVAILABLE` | The server has no topology yet. The client tries again in 5 s |
| `TOPOLOGY_NOT_SUBSCRIBED` | `topology:resync` from a socket that has not subscribed |
| `INTERNAL_ERROR` | Any other failure; the message never gives details |

## Limits (v0)

| Limit | Value |
|-------|-------|
| Size of a client message | 16 KB (`maxHttpBufferSize`, 1 MB by default). A larger message closes the socket ([server options](https://socket.io/docs/v4/server-options/#maxhttpbuffersize)). |
| Commands | 10 per 10 s and per connection |
| Size of a snapshot | Not limited: the whole graph travels in one message |
| Delivery | At most once. Order is kept on one connection, nothing is replayed after a drop: recovery is `subscribe`, then a snapshot ([delivery guarantees](https://socket.io/docs/v4/delivery-guarantees#at-most-once)). |
| Session | Checked at the handshake only: an open socket stays open after a logout or when its session expires |

## Examples

### First connection

```text
C→S  topology:subscribe {}           ack { data: { environmentId: "env_local_compose" } }
S→C  topology:snapshot               epoch A, revision 1
S→C  topology:status                 stale false
     ... the container of service db is created ...
S→C  topology:delta                  epoch A, 1 → 2
```

```json
{
  "event": "topology:snapshot",
  "payload": {
    "schemaVersion": 1,
    "environmentId": "env_local_compose",
    "capturedAt": "2026-10-21T09:12:40Z",
    "epoch": "7d2c9f1e-4b0a-4c55-9d2e-3a6f0b1c8e42",
    "revision": 1,
    "nodes": [
      { "id": "host:env_local_compose", "kind": "host", "label": "Docker host", "status": "healthy" },
      { "id": "net:demo_default", "kind": "network", "label": "demo_default", "status": "healthy" },
      { "id": "svc:demo:web", "kind": "service", "label": "web", "status": "healthy", "metadata": { "image": "demo-web", "composeProject": "demo", "composeService": "web", "ports": [] } }
    ],
    "edges": [
      { "id": "connects_to:svc:demo:web>net:demo_default", "source": "svc:demo:web", "target": "net:demo_default", "kind": "connects_to" },
      { "id": "runs_on:svc:demo:web>host:env_local_compose", "source": "svc:demo:web", "target": "host:env_local_compose", "kind": "runs_on" }
    ]
  },
  "timestamp": "2026-10-21T09:12:41Z"
}
```

The `web` service depends on `db` in its Compose file, but the edge only exists once both nodes do:

```json
{
  "event": "topology:delta",
  "payload": {
    "epoch": "7d2c9f1e-4b0a-4c55-9d2e-3a6f0b1c8e42",
    "baseRevision": 1,
    "revision": 2,
    "operations": [
      { "op": "node.upsert", "node": { "id": "svc:demo:db", "kind": "service", "label": "db", "status": "unknown", "metadata": { "image": "postgres:18", "composeProject": "demo", "composeService": "db", "ports": [] } } },
      { "op": "edge.upsert", "edge": { "id": "connects_to:svc:demo:db>net:demo_default", "source": "svc:demo:db", "target": "net:demo_default", "kind": "connects_to" } },
      { "op": "edge.upsert", "edge": { "id": "depends_on:svc:demo:web>svc:demo:db", "source": "svc:demo:web", "target": "svc:demo:db", "kind": "depends_on" } },
      { "op": "edge.upsert", "edge": { "id": "runs_on:svc:demo:db>host:env_local_compose", "source": "svc:demo:db", "target": "host:env_local_compose", "kind": "runs_on" } }
    ]
  },
  "timestamp": "2026-10-21T09:13:05Z"
}
```

### Nest restarts

```text
S→C  topology:delta       epoch A, 11 → 12     last message the client applied
     ---- Nest stops; the client reconnects by itself: new socket, no room ----
C→S  topology:subscribe {}
S→C  topology:snapshot    epoch B, revision 1  another epoch: adopted as it is
S→C  topology:status      stale false
S→C  topology:delta       epoch B, 1 → 2       baseRevision 1 is the client's: applied
```

### Collector outage

```text
S→C  topology:status      stale true, since 09:14:02Z   10 s after the first failed try
     ... the graph stays on screen, with a notice ...
S→C  topology:snapshot    epoch A, revision 13          the collector is back: everything is replaced
S→C  topology:status      stale false, since null
```
