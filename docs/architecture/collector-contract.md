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

## Events

| Field | Notes |
|-------|-------|
| `schemaVersion`, `environmentId` | As in the snapshot |
| `eventId` | Opaque, unique while the collector runs. For logs: Nest deduplicates on `sequence` |
| `sequence` | Starts at `1` when the collector starts, `+1` per event, **no gaps** (ignored Docker events use no number) |
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
| `container.died` | container / `die` | `{ exitCode, container }` |
| `container.destroyed` | container / `destroy` | `{}` |
| `container.health_changed` | container / `health_status: healthy` and `health_status: unhealthy` | `{ container }` |
| `network.created` | network / `create` | `{ network }` |
| `network.removed` | network / `destroy` | `{}` |
| `network.connected` | network / `connect` | `{ containerId }` |
| `network.disconnected` | network / `disconnect` | `{ containerId }` |
| `volume.created` | volume / `create` | `{ volume }` |
| `volume.removed` | volume / `destroy` | `{}` |

`exitCode` is an integer (Docker sends a string).

**Every container event carries the whole container object** (snapshot format), inspected by the collector right after the Docker event, except `destroyed`. So:

- Nest simply replaces its copy (upsert), with no merge.
- Nest derives the status from `data.container` (`state`, `health`), never from `type` alone: at `stop` the container is already `exited` or `removing`.
- An ignored Docker event leaves no wrong state for long: the next container event brings the current object.
- If the container is already gone when the collector inspects it (`docker rm -f`, fast `compose down`: `die` and `destroy` come about 20 ms apart), the collector emits nothing for that event. `container.died` is therefore not guaranteed; `container.destroyed` is enough.

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
