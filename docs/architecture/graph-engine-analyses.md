# Graph Engine analyses (cheat-sheet)

Short guide for **Nest** and **frontend** consumers. Full request/response shapes: [graph-contract.md](./graph-contract.md). Topology edge directions: [topology-mapping.md](./topology-mapping.md).

The browser does **not** call the Graph Engine in production; Nest is the gateway (`GRAPH_ENGINE_URL`).

## Which analysis when?

| Need | Call | Seeds? |
|------|------|--------|
| “If this node fails, what else breaks?” | `blast_radius` | **Yes** — the failed node(s) |
| “If this node is compromised, what can an attacker reach next?” | `attack_paths` | **Yes** — the compromised node(s) |
| “What are the most critical nodes in this topology?” | `critical_nodes` | **No** |

You can request several analyses in one `POST /api/v1/analyze` (`analyses` array).

## Seeds

- Required for `blast_radius` and `attack_paths` (`options.seedNodeIds`).
- Must be node ids that exist in the topology snapshot.
- Several seeds are allowed (union of results).
- `critical_nodes` ignores seeds.

## Edge kinds today (`depends_on` only for traversal)

For **blast** and **attack**, only `depends_on` edges are walked.  
`connects_to`, `exposes`, `runs_on`, and `mounts` are present in the snapshot but **ignored** by those two traversals.

| Analysis | Walk | Meaning |
|----------|------|---------|
| `blast_radius` | **Reversed** `depends_on` | Dependents of the seed (impact cone) |
| `attack_paths` | **Forward** `depends_on` | Paths from the seed toward its dependencies |
| `critical_nodes` | Ranking (not a walk) | Betweenness on the **full** graph + direct `depends_on` dependents |

Example (`api --depends_on--> db`):

- Seed `db` + `blast_radius` → nodes that depend on `db` (e.g. `api`, then `worker` if `worker → api`).
- Seed `api` + `attack_paths` → paths such as `api → db`, `api → cache`.
- Seed `ext:internet` + `attack_paths` → **no** path via `exposes` today (that edge kind is not traversed yet).

## Result keys

| Analysis id | Response key under `data.results` |
|-------------|-----------------------------------|
| `blast_radius` | `blastRadius` |
| `attack_paths` | `attackPaths` |
| `critical_nodes` | `criticalNodes` |

Envelope: `{ "data": { … } }` on success; `{ "error": { "code", "message" } }` on failure ([api-conventions.md](./api-conventions.md)).

## Stable examples

Checked-in topologies and golden analyze responses live under `services/graph-engine/fixtures/` (see Graph Engine README). Useful for Nest client tests without a live engine.
