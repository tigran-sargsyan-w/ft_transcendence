# Analyses API

How the browser asks for an analysis of the live topology. Nest sends the current topology to the Graph Engine ([Graph contract](./graph-contract.md)) and returns its results, saying whether they are still up to date. The browser never calls the engine.

Analyses go over HTTP and the answer goes to the user who asked, not to a room: an analysis is a question about nodes that user chose, and the realtime protocol does not carry it ([Realtime topology protocol](./realtime-topology.md)).

## Status

Implemented in the `analysis` module. It needs `GRAPH_ENGINE_URL`; without it every analysis answers `ANALYSIS_UNAVAILABLE`.

## Request

`POST /api/v1/environments/{environmentId}/analyses`, with a session ([Authentication and users](./auth-and-users.md)). The environment is the one the server follows.

```json
{
  "analyses": ["blast_radius", "critical_nodes"],
  "seedNodeIds": ["svc:demo:db"],
  "maxDepth": 5,
  "maxPaths": 20
}
```

| Field | Required | Notes |
|-------|----------|-------|
| `analyses` | yes | Not empty, no repeat. `blast_radius`, `attack_paths`, `critical_nodes` |
| `seedNodeIds` | for `blast_radius` and `attack_paths` | At most 50 ids of nodes of the topology |
| `maxDepth` | no | Integer, 1 to 10 (engine default 5) |
| `maxPaths` | no | Integer, 1 to 100 (engine default 20) |

An unknown field, or a value out of these limits, is refused with `VALIDATION_ERROR` before the engine is called. Which nodes each analysis walks, and the shape of the results, are in the [Graph contract](./graph-contract.md).

## Response

`200`, nothing is stored.

```json
{
  "data": {
    "environmentId": "env_local_compose",
    "topologyRevision": "6f1c…:12",
    "analyzedAt": "2026-11-02T10:00:01Z",
    "freshness": "current",
    "results": { "criticalNodes": { "nodes": [] } }
  }
}
```

`results` is the engine's, as it sends it. Its `reasons` are opaque strings: the engine may add codes, and a client must not reject one it does not know.

`topologyRevision` is the revision of the topology that was sent (`<epoch>:<revision>`, the one of the realtime snapshot). `freshness` says what the results are worth now:

| Value | Meaning |
|-------|---------|
| `current` | The engine echoed that revision, and the topology is still at it |
| `stale` | The topology changed while the engine worked |
| `unverifiable` | The engine did not echo a revision: nothing proves which topology the results are about |

Show `stale` and `unverifiable` results as such, never as "no risk found": an empty list means something only when `freshness` is `current`.

The same analysis of the same revision, asked by several users at once, is one call to the engine.

## Errors

Shape and codes: [API conventions](./api-conventions.md).

| Status | Code | When |
|--------|------|------|
| 400 | `VALIDATION_ERROR` | The request breaks the table above |
| 400 | the engine's (`GRAPH_MISSING_SEEDS`, `GRAPH_INVALID_TOPOLOGY`…) | The engine refuses the request, for example a seed that is not in the topology. Its message is passed on |
| 404 | `TOPOLOGY_UNKNOWN_ENVIRONMENT` | The server does not follow that environment |
| 502 | `GRAPH_ENGINE_FAILURE` | The engine failed on its own |
| 503 | `TOPOLOGY_UNAVAILABLE` | No topology yet |
| 503 | `ANALYSIS_UNAVAILABLE` | No engine configured, not reachable, no answer within 5 seconds, or an answer that is not the contract's (including one about another environment, or another revision than the one sent) |
