# Collector Contract

**Status: implemented v0.** The Collector side has been validated against the running implementation and automated tests. Final consumer confirmation from the Nest topology side is still required before closing the contract task.

The Docker collector serves the current state of a Docker/Compose environment (a **snapshot**) and a numbered stream of **events** over internal HTTP/JSON. Nest reads both to build the topology (`nodes` / `edges`, see [Graph contract](./graph-contract.md)). Nothing else calls the collector.

| Point | Status |
|-------|--------|
| HTTP/JSON transport | Decided (Tigran) |
| Docker reached through a read-only socket proxy, no direct `docker.sock` mount | Decided (Tigran) |
| Nest reads from the collector (snapshot + long polling) | Decided and implemented |

## Roles

- **Collector:** talks to Docker, normalizes, numbers the events, keeps a short in-memory buffer. It reports what Docker says: it builds no nodes or edges and analyzes nothing. One collector serves one environment.
- **Nest:** never talks to Docker. Turns snapshot + events into `nodes` / `edges` and keeps the live topology.
- **Not in v0:** authentication between services, several environments, sources other than Docker/Compose, persistence of the event buffer.

## Conventions

Same as the [Graph contract](./graph-contract.md): `schemaVersion` (integer, starts at `1`), `environmentId` (opaque, e.g. `env_local_compose`), opaque string ids, ISO 8601 UTC dates, `camelCase` fields, `{ "data": … }` on success and `{ "error": { "code", "message" } }` on failure ([API conventions](./api-conventions.md)). Ids here are Docker's own; the stable graph ids (`svc_api`…) are built by Nest.

## Snapshot

The full Docker state collected for one snapshot: `{ schemaVersion, streamId, environmentId, capturedAt, sequence, containers, networks, volumes }`.

`streamId` identifies the current Collector event-stream session. It changes whenever the Collector resets the Docker event stream. `streamId` and `sequence` are captured together from the same checkpoint immediately before Docker state is collected.

Because Docker state is collected after the checkpoint is captured, the resulting snapshot may already reflect a change represented by an event with a higher sequence. Nest must therefore apply every event after `snapshot.sequence`; applying an update that the snapshot already reflects must be safe and idempotent.

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
| `ports` | Published ports `{ containerPort, protocol, hostIp, hostPort }`, read from the port bindings. Exact duplicate bindings are removed, but bindings with different `hostIp` values remain distinct. `hostIp` is `0.0.0.0` when Docker leaves it empty |
| `networks` | `{ networkId, name, ipv4Address }`. `networkId` and `ipv4Address` are empty while the container is not attached (just created, or exited) |
| `mounts` | `{ type, source, destination, readOnly }`. For Docker volumes, `source` is the volume **name**. For bind mounts, the host path is never exposed and `source` is `[redacted]` |

**Network:** `id`, `name`, `driver`, `internal`, `labels`. **Volume:** `name` (Docker volumes have no id), `driver`, `labels`.

Environment variables and command lines are never forwarded: they routinely contain secrets.

## Events

| Field | Notes |
|-------|-------|
| `schemaVersion`, `environmentId` | As in the snapshot |
| `streamId` | Identifies the current Collector stream session. All events in the same stream share it; it changes when the Docker event stream is reset |
| `eventId` | Opaque and unique within one `streamId`. It is derived from the sequence, so use `streamId` + `sequence` as the stable event identity across resets |
| `sequence` | Starts at `1` for each `streamId`, `+1` per emitted event, **no gaps** (ignored Docker events use no number) |
| `occurredAt` | ISO 8601 UTC, from Docker |
| `type` | See below |
| `resource` | `{ kind, id }`: `container`, `network` or `volume` (its id is its name). For `network.connected` / `network.disconnected` it is the network |
| `data` | Depends on `type` |

Nest ignores an unknown `type` (and still records its `sequence`), so types can be added without a version bump.

| Type | Docker `Type` / `Action` | `data` |
|------|--------------------------|--------|
| `container.created` | container / `create` | `{ container }` |
| `container.started` | container / `start` | `{ container }` |
| `container.stopped` | container / `stop` | `{ container }` |
| `container.died` | container / `die` | `{ exitCode?, container? }` |
| `container.destroyed` | container / `destroy` | `{}` |
| `container.health_changed` | container / `health_status: healthy` and `health_status: unhealthy` | `{ container }` |
| `network.created` | network / `create` | `{ network }` |
| `network.removed` | network / `destroy` | `{}` |
| `network.connected` | network / `connect` | `{ containerId }` |
| `network.disconnected` | network / `disconnect` | `{ containerId }` |
| `volume.created` | volume / `create` | `{ volume }` |
| `volume.removed` | volume / `destroy` | `{}` |

`exitCode` is an integer when Docker supplies a valid integer value (Docker sends it as a string).

For `container.created`, `container.started`, `container.stopped`, and `container.health_changed`, the Collector inspects the container immediately after the Docker event and emits the event only when that normalized container object is available. Nest can upsert the whole object with no merge and derives state from `data.container`, not from the event type alone.

`container.died` is different: it is emitted even if the container disappears before `docker inspect` succeeds (for example a fast `docker run --rm`). `exitCode` is included when valid, and `container` is included only when inspection still succeeds. `container.destroyed` never carries a container object.

An ignored Docker event leaves no wrong state for long: the next emitted container event carries the current normalized object.

Everything else Docker emits is ignored (`kill`, `rename`, `pause`, `exec_*`, volume `mount` / `unmount`…). Match `Action` exactly, as it can carry a suffix (`health_status: healthy`, `exec_create: true `). A healthcheck emits `exec_*` at every run, so the collector must filter them.

### What Docker really emits

Captured with `docker events` on Docker 29.3.0 and Compose 5.1.1 (`demo` project: `db` with a healthcheck and a volume, `api` that crashes after 10 s, `web` that publishes a port).

| Scenario | Docker events, in order |
|----------|-------------------------|
| `compose up` | network `create`, volume `create`, container `create` ×3, then per container (following `depends_on`): network `connect`, `start`. A healthcheck adds `health_status: healthy` once, when the status changes |
| Crash | network `disconnect`, then `die` (no `stop`) |
| `stop`, `compose down` | `kill`, network `disconnect`, `stop`, `die`, `destroy`: `stop` comes before `die` |
| `up --force-recreate` | `create` (temporary name `<oldId12>_<name>`), `destroy` (old), `rename` (ignored: the temporary name stays until the next container event), network `connect`, `start`. Two instances of the service coexist for a moment |

## Stable service identity

A container id changes each time Compose recreates it, and a graph node must not vanish at each redeploy. So a service is identified by **`compose.project` + `compose.service`** (labels `com.docker.compose.project` and `.service`); the container `id` only identifies one instance. Nest keys its nodes by service, and a service can have several instances at once (see the recreation).

Limits: a container outside Compose has `compose: null` (Nest falls back to `name`); scaled services and one-off containers (`docker compose run`) are not distinguished in v0, they look like one more instance.

## Visible dependencies

| Data | Where | Edge kind (graph contract) |
|------|-------|----------------------------|
| Shared network | `container.networks[].name` | `connects_to` |
| Published port | `container.ports[]` | `exposes` |
| Mount | `container.mounts[]` | `mounts` |
| Compose dependency | `container.compose.dependsOn` | `depends_on` |
| The host | nothing: one host per `environmentId` | `runs_on`, created by Nest |

The final mapping and edge direction belong to the topology module, with Camille (decision 0.4).

`depends_on` is read from the label `com.docker.compose.depends_on`: on Compose 5.1.1 a comma-separated list of `service:condition:restart`, empty when there is none (`db:service_healthy:false,cache:service_started:true`). The collector keeps the names only (`["db", "cache"]`). Older Compose versions may lack the label: `dependsOn` is then `[]`, the same as "no dependency" (accepted for the POC).

## Order, gaps and resynchronization

Nest tracks both `streamId` and `sequence`. Within one stream, it ignores an event whose `sequence` is lower than or equal to the last applied one.

A resynchronization is required when:

- Nest starts without existing topology state;
- `COLLECTOR_SEQUENCE_EXPIRED` is returned;
- an event sequence contains a gap (an event is not `lastApplied + 1`, or a response does not start at `after + 1`);
- the event-batch `streamId` differs from the current snapshot/stream `streamId`.

```text
1. GET /api/v1/snapshot
2. replace the whole in-memory state with it
3. currentStreamId = snapshot.streamId
4. lastApplied = snapshot.sequence
5. loop: GET /api/v1/events?after=lastApplied
6. if response.streamId != currentStreamId: discard the batch and restart from step 1
7. otherwise apply events in order and advance lastApplied
```

Nest does not send `streamId` back to the Collector in v0; it validates the `streamId` returned with each event batch.

## HTTP surface (v0)

Internal only, base path `/api/v1`.

| Method | Path | Answer |
|--------|------|--------|
| `GET` | `/health` | Collector/Docker availability and Docker version information |
| `GET` | `/snapshot` | `{ "data": <snapshot> }` |
| `GET` | `/events?after=<sequence>` | `{ "data": { "streamId": "…", "events": [] } }`: the events with a `sequence` greater than `after` (integer, `0` or more) |

`/events` is a **long poll**: with nothing new, the collector waits about 25 s and answers with an empty list while still returning the current `streamId`. Nest's HTTP timeout must be longer (for example 35 s) and Nest calls again right after each answer. The Collector keeps the latest **1000 events** in memory by default.

### Errors

| Code | HTTP | When |
|------|------|------|
| `COLLECTOR_SEQUENCE_EXPIRED` | `409` | `after` is older than the buffer, **or greater than the latest sequence** (for example a collector that restarted) |
| `COLLECTOR_INVALID_REQUEST` | `400` | Missing or malformed `after` |
| `COLLECTOR_DOCKER_UNAVAILABLE` | `503` | The collector cannot reach Docker |

**Why Nest reads:** this direction is now the implemented v0 design. Nest sets its own pace, restart/resynchronization is straightforward, the Collector does not need to know Nest, and Nest exposes no additional ingestion endpoint to the Collector.

## Security

- **Docker access (decided):** read-only socket proxy, no direct `docker.sock` mount in the Collector. The current proxy enables only the Docker API sections needed for containers, networks, volumes, events, ping and version, with `POST=0`. Collector calls are read-only and currently use `/_ping`, `/version`, `/containers/json?all=1`, `/containers/{id}/json`, `/networks`, `/networks/{id}`, `/volumes`, `/volumes/{name}`, and `/events`.
- In the final application topology, the Collector and Docker proxy are internal-only and must not be routed through Nginx or exposed publicly. The current development Compose configuration temporarily publishes both services on `127.0.0.1` for local debugging; those loopback-only development bindings are not part of the final deployment exposure model.
- Nest is the only application consumer of the Collector. No authentication between Collector and Nest exists in v0 (see [Open questions](#open-questions)).
- Environment variables and command lines are never forwarded. Bind-mount host paths are redacted.

## Examples

One run of a small Compose project, `demo`: `db` (healthcheck, volume), `api` (depends on `db`, crashes after 10 s) and `web` (depends on `api`, publishes port 8080). Names and timestamps come from a real run, and the container objects follow what `docker inspect` returned after each event. Ids are shortened to 12 characters (Docker sends 64). They can be used as fixtures for the topology module.

### Snapshot after `docker compose up`

`GET /api/v1/snapshot`, right after event 12:

```json
{
  "data": {
    "schemaVersion": 1, "streamId": "stream_demo_01", "environmentId": "env_local_compose", "capturedAt": "2026-09-23T15:38:47.960Z", "sequence": 12,
    "containers": [
      { "id": "6c28d5dff0b8", "name": "demo-db-1", "image": "busybox:1.37", "state": "running", "health": "healthy", "labels": {},
        "compose": { "project": "demo", "service": "db", "dependsOn": [] }, "ports": [],
        "networks": [{ "networkId": "e5b899c59c6a", "name": "demo_default", "ipv4Address": "172.18.0.2" }],
        "mounts": [{ "type": "volume", "source": "demo_db_data", "destination": "/data", "readOnly": false }] },
      { "id": "4589c1f3ac3c", "name": "demo-api-1", "image": "busybox:1.37", "state": "running", "health": "none", "labels": {},
        "compose": { "project": "demo", "service": "api", "dependsOn": ["db"] }, "ports": [],
        "networks": [{ "networkId": "e5b899c59c6a", "name": "demo_default", "ipv4Address": "172.18.0.3" }],
        "mounts": [] },
      { "id": "b183ed94a57c", "name": "demo-web-1", "image": "busybox:1.37", "state": "running", "health": "none", "labels": {},
        "compose": { "project": "demo", "service": "web", "dependsOn": ["api"] }, "ports": [{ "containerPort": 80, "protocol": "tcp", "hostIp": "0.0.0.0", "hostPort": 8080 }],
        "networks": [{ "networkId": "e5b899c59c6a", "name": "demo_default", "ipv4Address": "172.18.0.4" }],
        "mounts": [] }
    ],
    "networks": [{ "id": "e5b899c59c6a", "name": "demo_default", "driver": "bridge", "internal": false, "labels": {} }],
    "volumes": [{ "name": "demo_db_data", "driver": "local", "labels": {} }]
  }
}
```

### Events of the `up`

`GET /api/v1/events?after=0`: events 1 to 12. Replaying them from an empty state gives exactly the snapshot above. A container just created has `state: "created"`, `health: "none"` and a network entry with an empty `networkId`; `db` goes `starting`, then `healthy` (event 8); `network.connected` only carries the container id.

```json
{
  "data": {
    "streamId": "stream_demo_01",
    "events": [
      {
        "schemaVersion": 1, "streamId": "stream_demo_01", "environmentId": "env_local_compose", "eventId": "evt_00000001", "sequence": 1,
        "occurredAt": "2026-09-23T15:38:44.353Z", "type": "network.created",
        "resource": { "kind": "network", "id": "e5b899c59c6a" },
        "data": { "network": { "id": "e5b899c59c6a", "name": "demo_default", "driver": "bridge", "internal": false, "labels": {} } }
      },
      {
        "schemaVersion": 1, "streamId": "stream_demo_01", "environmentId": "env_local_compose", "eventId": "evt_00000002", "sequence": 2,
        "occurredAt": "2026-09-23T15:38:44.357Z", "type": "volume.created",
        "resource": { "kind": "volume", "id": "demo_db_data" },
        "data": { "volume": { "name": "demo_db_data", "driver": "local", "labels": {} } }
      },
      {
        "schemaVersion": 1, "streamId": "stream_demo_01", "environmentId": "env_local_compose", "eventId": "evt_00000003", "sequence": 3,
        "occurredAt": "2026-09-23T15:38:44.442Z", "type": "container.created",
        "resource": { "kind": "container", "id": "6c28d5dff0b8" },
        "data": { "container": { "id": "6c28d5dff0b8", "name": "demo-db-1", "image": "busybox:1.37", "state": "created", "health": "none", "labels": {},
          "compose": { "project": "demo", "service": "db", "dependsOn": [] }, "ports": [],
          "networks": [{ "networkId": "", "name": "demo_default", "ipv4Address": "" }],
          "mounts": [{ "type": "volume", "source": "demo_db_data", "destination": "/data", "readOnly": false }] } }
      },
      {
        "schemaVersion": 1, "streamId": "stream_demo_01", "environmentId": "env_local_compose", "eventId": "evt_00000004", "sequence": 4,
        "occurredAt": "2026-09-23T15:38:44.542Z", "type": "container.created",
        "resource": { "kind": "container", "id": "4589c1f3ac3c" },
        "data": { "container": { "id": "4589c1f3ac3c", "name": "demo-api-1", "image": "busybox:1.37", "state": "created", "health": "none", "labels": {},
          "compose": { "project": "demo", "service": "api", "dependsOn": ["db"] }, "ports": [],
          "networks": [{ "networkId": "", "name": "demo_default", "ipv4Address": "" }],
          "mounts": [] } }
      },
      {
        "schemaVersion": 1, "streamId": "stream_demo_01", "environmentId": "env_local_compose", "eventId": "evt_00000005", "sequence": 5,
        "occurredAt": "2026-09-23T15:38:44.620Z", "type": "container.created",
        "resource": { "kind": "container", "id": "b183ed94a57c" },
        "data": { "container": { "id": "b183ed94a57c", "name": "demo-web-1", "image": "busybox:1.37", "state": "created", "health": "none", "labels": {},
          "compose": { "project": "demo", "service": "web", "dependsOn": ["api"] }, "ports": [{ "containerPort": 80, "protocol": "tcp", "hostIp": "0.0.0.0", "hostPort": 8080 }],
          "networks": [{ "networkId": "", "name": "demo_default", "ipv4Address": "" }],
          "mounts": [] } }
      },
      {
        "schemaVersion": 1, "streamId": "stream_demo_01", "environmentId": "env_local_compose", "eventId": "evt_00000006", "sequence": 6,
        "occurredAt": "2026-09-23T15:38:44.902Z", "type": "network.connected",
        "resource": { "kind": "network", "id": "e5b899c59c6a" },
        "data": { "containerId": "6c28d5dff0b8" }
      },
      {
        "schemaVersion": 1, "streamId": "stream_demo_01", "environmentId": "env_local_compose", "eventId": "evt_00000007", "sequence": 7,
        "occurredAt": "2026-09-23T15:38:44.928Z", "type": "container.started",
        "resource": { "kind": "container", "id": "6c28d5dff0b8" },
        "data": { "container": { "id": "6c28d5dff0b8", "name": "demo-db-1", "image": "busybox:1.37", "state": "running", "health": "starting", "labels": {},
          "compose": { "project": "demo", "service": "db", "dependsOn": [] }, "ports": [],
          "networks": [{ "networkId": "e5b899c59c6a", "name": "demo_default", "ipv4Address": "172.18.0.2" }],
          "mounts": [{ "type": "volume", "source": "demo_db_data", "destination": "/data", "readOnly": false }] } }
      },
      {
        "schemaVersion": 1, "streamId": "stream_demo_01", "environmentId": "env_local_compose", "eventId": "evt_00000008", "sequence": 8,
        "occurredAt": "2026-09-23T15:38:46.976Z", "type": "container.health_changed",
        "resource": { "kind": "container", "id": "6c28d5dff0b8" },
        "data": { "container": { "id": "6c28d5dff0b8", "name": "demo-db-1", "image": "busybox:1.37", "state": "running", "health": "healthy", "labels": {},
          "compose": { "project": "demo", "service": "db", "dependsOn": [] }, "ports": [],
          "networks": [{ "networkId": "e5b899c59c6a", "name": "demo_default", "ipv4Address": "172.18.0.2" }],
          "mounts": [{ "type": "volume", "source": "demo_db_data", "destination": "/data", "readOnly": false }] } }
      },
      {
        "schemaVersion": 1, "streamId": "stream_demo_01", "environmentId": "env_local_compose", "eventId": "evt_00000009", "sequence": 9,
        "occurredAt": "2026-09-23T15:38:47.610Z", "type": "network.connected",
        "resource": { "kind": "network", "id": "e5b899c59c6a" },
        "data": { "containerId": "4589c1f3ac3c" }
      },
      {
        "schemaVersion": 1, "streamId": "stream_demo_01", "environmentId": "env_local_compose", "eventId": "evt_00000010", "sequence": 10,
        "occurredAt": "2026-09-23T15:38:47.628Z", "type": "container.started",
        "resource": { "kind": "container", "id": "4589c1f3ac3c" },
        "data": { "container": { "id": "4589c1f3ac3c", "name": "demo-api-1", "image": "busybox:1.37", "state": "running", "health": "none", "labels": {},
          "compose": { "project": "demo", "service": "api", "dependsOn": ["db"] }, "ports": [],
          "networks": [{ "networkId": "e5b899c59c6a", "name": "demo_default", "ipv4Address": "172.18.0.3" }],
          "mounts": [] } }
      },
      {
        "schemaVersion": 1, "streamId": "stream_demo_01", "environmentId": "env_local_compose", "eventId": "evt_00000011", "sequence": 11,
        "occurredAt": "2026-09-23T15:38:47.902Z", "type": "network.connected",
        "resource": { "kind": "network", "id": "e5b899c59c6a" },
        "data": { "containerId": "b183ed94a57c" }
      },
      {
        "schemaVersion": 1, "streamId": "stream_demo_01", "environmentId": "env_local_compose", "eventId": "evt_00000012", "sequence": 12,
        "occurredAt": "2026-09-23T15:38:47.923Z", "type": "container.started",
        "resource": { "kind": "container", "id": "b183ed94a57c" },
        "data": { "container": { "id": "b183ed94a57c", "name": "demo-web-1", "image": "busybox:1.37", "state": "running", "health": "none", "labels": {},
          "compose": { "project": "demo", "service": "web", "dependsOn": ["api"] }, "ports": [{ "containerPort": 80, "protocol": "tcp", "hostIp": "0.0.0.0", "hostPort": 8080 }],
          "networks": [{ "networkId": "e5b899c59c6a", "name": "demo_default", "ipv4Address": "172.18.0.4" }],
          "mounts": [] } }
      }
    ]
  }
}
```

### The crash and the recreation

`GET /api/v1/events?after=12`: events 13 to 18.

- 13-14: `api` crashes with exit code 1: the network is disconnected, then the container dies (`state: "exited"`, network still listed, without IP).
- 15-18: `docker compose up -d --force-recreate api`: the new container (new `id`) is created under a temporary name, the old one is destroyed, then the new one is connected and started. Only the last object has the final name. The service `demo` / `api` is the same throughout.

```json
{
  "data": {
    "streamId": "stream_demo_01",
    "events": [
      {
        "schemaVersion": 1, "streamId": "stream_demo_01", "environmentId": "env_local_compose", "eventId": "evt_00000013", "sequence": 13,
        "occurredAt": "2026-09-23T15:38:57.830Z", "type": "network.disconnected",
        "resource": { "kind": "network", "id": "e5b899c59c6a" },
        "data": { "containerId": "4589c1f3ac3c" }
      },
      {
        "schemaVersion": 1, "streamId": "stream_demo_01", "environmentId": "env_local_compose", "eventId": "evt_00000014", "sequence": 14,
        "occurredAt": "2026-09-23T15:38:57.839Z", "type": "container.died",
        "resource": { "kind": "container", "id": "4589c1f3ac3c" },
        "data": { "exitCode": 1, "container": { "id": "4589c1f3ac3c", "name": "demo-api-1", "image": "busybox:1.37", "state": "exited", "health": "none", "labels": {},
          "compose": { "project": "demo", "service": "api", "dependsOn": ["db"] }, "ports": [],
          "networks": [{ "networkId": "e5b899c59c6a", "name": "demo_default", "ipv4Address": "" }],
          "mounts": [] } }
      },
      {
        "schemaVersion": 1, "streamId": "stream_demo_01", "environmentId": "env_local_compose", "eventId": "evt_00000015", "sequence": 15,
        "occurredAt": "2026-09-23T15:39:07.184Z", "type": "container.created",
        "resource": { "kind": "container", "id": "ea2201557210" },
        "data": { "container": { "id": "ea2201557210", "name": "4589c1f3ac3c_demo-api-1", "image": "busybox:1.37", "state": "created", "health": "none", "labels": {},
          "compose": { "project": "demo", "service": "api", "dependsOn": ["db"] }, "ports": [],
          "networks": [{ "networkId": "", "name": "demo_default", "ipv4Address": "" }],
          "mounts": [] } }
      },
      {
        "schemaVersion": 1, "streamId": "stream_demo_01", "environmentId": "env_local_compose", "eventId": "evt_00000016", "sequence": 16,
        "occurredAt": "2026-09-23T15:39:07.209Z", "type": "container.destroyed",
        "resource": { "kind": "container", "id": "4589c1f3ac3c" },
        "data": {}
      },
      {
        "schemaVersion": 1, "streamId": "stream_demo_01", "environmentId": "env_local_compose", "eventId": "evt_00000017", "sequence": 17,
        "occurredAt": "2026-09-23T15:39:07.954Z", "type": "network.connected",
        "resource": { "kind": "network", "id": "e5b899c59c6a" },
        "data": { "containerId": "ea2201557210" }
      },
      {
        "schemaVersion": 1, "streamId": "stream_demo_01", "environmentId": "env_local_compose", "eventId": "evt_00000018", "sequence": 18,
        "occurredAt": "2026-09-23T15:39:07.976Z", "type": "container.started",
        "resource": { "kind": "container", "id": "ea2201557210" },
        "data": { "container": { "id": "ea2201557210", "name": "demo-api-1", "image": "busybox:1.37", "state": "running", "health": "none", "labels": {},
          "compose": { "project": "demo", "service": "api", "dependsOn": ["db"] }, "ports": [],
          "networks": [{ "networkId": "e5b899c59c6a", "name": "demo_default", "ipv4Address": "172.18.0.3" }],
          "mounts": [] } }
      }
    ]
  }
}
```

## Open questions

1. **Authentication between services.** None in v0: internal network only. Revisit if the deployment model changes.
2. **Scaled services and one-off containers.** The `container-number` and `oneoff` labels are not exposed yet. Do they need to be?
3. **Stale data after ignored events.** `docker rename` and a standalone `docker network connect` / `disconnect` can leave some container metadata stale until the next container event or a full resynchronization, because network events only carry `containerId`.
4. **Topology semantics for network connect/disconnect.** `network.disconnected` fires on every stop or crash while container inspection may still list the network. Final graph-edge behavior belongs to the Nest topology module.
