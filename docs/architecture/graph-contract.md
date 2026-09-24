# Graph Contract

## In one sentence

Nest sends a JSON snapshot of the infrastructure graph to the Graph Engine; the engine returns analysis results (blast radius, attack paths, critical nodes) as JSON with the same node and edge ids.

## Who uses it

- NestJS backend — builds topology as `nodes`/`edges`, sends snapshots to the Graph Engine, may cache or forward results
- Graph Engine (Python) — computes analyses
- Frontend (React Flow) — usually via Nest, not by calling the Graph Engine directly

Transport between Nest and the Graph Engine is plain HTTP/JSON (see ADR 0001). Response envelopes follow [API conventions](./api-conventions.md) (`data` / `error`).

This document describes the **v0** contract. Fields can grow; renames of existing required fields should be avoided without a version bump.

## Boundaries (team agreement)

These rules keep ownership clear between collector, Nest, Graph Engine, and frontend:

| Responsibility | Owner |
|----------------|--------|
| Talk to Docker / emit normalized infra events | Collector (infra) |
| Build and maintain topology as `nodes` / `edges` | Nest |
| Run blast radius, attack paths, critical nodes, scoring | Graph Engine (Python) |
| Live graph to the browser + reconnect recovery | Nest (WebSocket) |
| Call Graph Engine analyze API | Nest (HTTP); browser talks to Nest only |

Additional rules:

- The Graph Engine is **analysis only**. It does not discover Docker state and does not turn raw events into a graph by itself. The team may still co-define the event → node/edge mapping in docs.
- Nest → Graph Engine analyze uses a **full topology snapshot** per request (this contract).
- Frontend live updates: **full topology snapshot on connect/reconnect**, then **small deltas** for live changes. Nest owns recovery so the UI never keeps a silent outdated graph. Delta shapes for the socket can live under realtime docs; they are not part of the Graph Engine HTTP API.
- **REST vs WebSocket:** auth, history, pagination, and analyze go over HTTP. Live topology/incident updates go over the WebSocket. Analyze is never done over the socket (frontend → Nest → Graph Engine over HTTP).

## Plain-language terms

| Term | Meaning |
|------|---------|
| Topology snapshot | Full picture of services/networks/links at one moment |
| Seed node | Starting point for an analysis (e.g. the service that went down) |
| Blast radius | Which other nodes are likely affected from the seed(s) |
| Attack path | Ordered chain of nodes/edges from a seed through the graph |
| Critical node | Node whose failure or compromise would hurt many others |
| Score | Number from `0` to `1` used for ranking (higher = more important / riskier) |

## Topology snapshot

A topology is a directed graph of infrastructure entities at a point in time.

```json
{
  "schemaVersion": 1,
  "environmentId": "env_local_compose",
  "capturedAt": "2026-09-14T10:00:00Z",
  "topologyRevision": "rev_42",
  "nodes": [],
  "edges": []
}
```

| Field | Required | Notes |
|-------|----------|--------|
| `schemaVersion` | yes | Integer; start at `1` |
| `environmentId` | yes | Opaque id of the monitored environment |
| `capturedAt` | yes | ISO 8601 UTC |
| `topologyRevision` | no | Opaque revision id from Nest; echoed in the analyze response so Nest can ignore stale results |
| `nodes` | yes | Array of nodes |
| `edges` | yes | Array of edges |

### Node

```json
{
  "id": "svc_api",
  "kind": "service",
  "label": "api",
  "status": "healthy",
  "metadata": {
    "image": "backend:latest",
    "composeService": "backend"
  }
}
```

| Field | Required | Notes |
|-------|----------|--------|
| `id` | yes | Stable opaque id within the environment |
| `kind` | yes | See kinds below |
| `label` | yes | Human-readable name for UI |
| `status` | yes | See statuses below |
| `metadata` | no | Free-form object; discovery-specific details |

**Kinds (v0):** `service` · `network` · `volume` · `host` · `external`

**Statuses (v0):** `healthy` · `degraded` · `down` · `unknown` · `compromised`

Unknown kinds/statuses should be rejected or normalized by the receiver; do not invent silent aliases.

### Edge

```json
{
  "id": "edge_api_db",
  "source": "svc_api",
  "target": "svc_db",
  "kind": "depends_on",
  "metadata": {}
}
```

| Field | Required | Notes |
|-------|----------|--------|
| `id` | yes | Stable opaque id within the environment |
| `source` | yes | Node id |
| `target` | yes | Node id |
| `kind` | yes | See edge kinds below |
| `metadata` | no | Free-form object |

**Edge kinds (v0):** `depends_on` · `connects_to` · `exposes` · `runs_on` · `mounts`

Direction matters for blast radius and attack-path analysis (`source` → `target`).

For v0 analysis direction:

- `blast_radius` walks the **reversed** dependency graph (if `db` fails, who depends on it?)
- `attack_paths` walks edges **forward** from a compromised seed (what can it reach?)

## Graph Engine HTTP surface (v0)

Base path on the Graph Engine service (internal):

```text
/api/v1
```

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/api/v1/health` | Liveness; no topology required |
| `POST` | `/api/v1/analyze` | Run one or more analyses on a topology snapshot |

The browser should not call these endpoints in production; Nest is the gateway.

### Analyze request

```json
{
  "topology": {
    "schemaVersion": 1,
    "environmentId": "env_local_compose",
    "capturedAt": "2026-09-14T10:00:00Z",
    "topologyRevision": "rev_42",
    "nodes": [],
    "edges": []
  },
  "analyses": ["blast_radius", "attack_paths", "critical_nodes"],
  "options": {
    "seedNodeIds": ["svc_api"],
    "maxDepth": 5,
    "maxPaths": 20
  }
}
```

| Field | Required | Notes |
|-------|----------|--------|
| `topology` | yes | Full snapshot as defined above |
| `analyses` | yes | Non-empty list of analysis ids |
| `options.seedNodeIds` | for some analyses | e.g. blast radius origin(s) |
| `options.maxDepth` | no | Traversal limit |
| `options.maxPaths` | no | Cap for path enumeration |

**Analysis ids (v0):**

| Id | Needs `seedNodeIds` | Result key |
|----|---------------------|------------|
| `blast_radius` | yes | `blastRadius` |
| `attack_paths` | yes | `attackPaths` |
| `critical_nodes` | no | `criticalNodes` |

### Analyze success response

```json
{
  "data": {
    "schemaVersion": 1,
    "environmentId": "env_local_compose",
    "topologyRevision": "rev_42",
    "analyzedAt": "2026-09-14T10:00:01Z",
    "results": {
      "blastRadius": {
        "seedNodeIds": ["svc_db"],
        "affectedNodeIds": ["svc_db", "svc_api", "svc_worker"],
        "affectedEdgeIds": ["edge_api_db", "edge_worker_api"],
        "depthByNodeId": {
          "svc_db": 0,
          "svc_api": 1,
          "svc_worker": 2
        }
      },
      "attackPaths": {
        "seedNodeIds": ["svc_api"],
        "paths": [
          {
            "nodeIds": ["svc_api", "svc_db"],
            "edgeIds": ["edge_api_db"],
            "score": 0.8
          }
        ]
      },
      "criticalNodes": {
        "nodes": [
          {
            "nodeId": "svc_db",
            "score": 0.95,
            "reasons": ["high_betweenness", "many_dependents"]
          }
        ]
      }
    }
  }
}
```

Only requested analyses appear under `results`. Scores are floats in `[0, 1]` unless a later version documents otherwise.

If the request topology included `topologyRevision`, the success response must echo the same value under `data.topologyRevision`. If it was omitted, the response omits the field.

**`affectedEdgeIds` (blast radius):** only edges that were **used during the traversal** (the blast tree: parent → child links discovered while walking). Do **not** include every edge that merely connects two affected nodes. This keeps UI highlights aligned with the actual impact path.

### Analyze error response

```json
{
  "error": {
    "code": "GRAPH_INVALID_TOPOLOGY",
    "message": "Edge edge_x references unknown node svc_missing"
  }
}
```

Suggested error codes (v0):

| Code | When |
|------|------|
| `GRAPH_INVALID_TOPOLOGY` | Malformed graph, missing node refs, bad kinds |
| `GRAPH_UNKNOWN_ANALYSIS` | Unknown id in `analyses` |
| `GRAPH_MISSING_SEEDS` | Analysis requires `seedNodeIds` and none were given |
| `GRAPH_ENGINE_FAILURE` | Unexpected internal failure |

## TypeScript sketch (informative)

Optional mirror for Nest/React. Not a runtime dependency of the Graph Engine.

```ts
type NodeKind = 'service' | 'network' | 'volume' | 'host' | 'external';
type NodeStatus = 'healthy' | 'degraded' | 'down' | 'unknown' | 'compromised';
type EdgeKind = 'depends_on' | 'connects_to' | 'exposes' | 'runs_on' | 'mounts';
type AnalysisId = 'blast_radius' | 'attack_paths' | 'critical_nodes';

interface GraphNode {
  id: string;
  kind: NodeKind;
  label: string;
  status: NodeStatus;
  metadata?: Record<string, unknown>;
}

interface GraphEdge {
  id: string;
  source: string;
  target: string;
  kind: EdgeKind;
  metadata?: Record<string, unknown>;
}

interface TopologySnapshot {
  schemaVersion: 1;
  environmentId: string;
  capturedAt: string;
  topologyRevision?: string;
  nodes: GraphNode[];
  edges: GraphEdge[];
}

interface AnalyzeRequest {
  topology: TopologySnapshot;
  analyses: AnalysisId[];
  options?: {
    seedNodeIds?: string[];
    maxDepth?: number;
    maxPaths?: number;
  };
}
```

## Frontend mapping (informative)

React Flow can map `nodes[].id/label/status/kind` to custom nodes and `edges[].source/target` to edges. Analysis highlights should prefer `affectedNodeIds` / `affectedEdgeIds` / path `nodeIds` rather than inventing a parallel id scheme.

## Out of scope for v0

- Auth on the Graph Engine (internal network only at first)
- Persistence inside the Graph Engine
- Streaming/WebSocket analysis
- Partial topology diffs on the Graph Engine HTTP API (analyze uses a full snapshot per request; frontend live deltas are Nest ↔ browser)
