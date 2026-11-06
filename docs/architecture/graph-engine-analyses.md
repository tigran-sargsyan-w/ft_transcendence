# Graph Engine analyses (cheat-sheet)

Which analysis to call, and who talks to whom. Deep shapes and scoring live in linked docs — prefer those over copying rules here.

| Audience | Doc |
|----------|-----|
| Frontend → Nest | [analysis-api.md](./analysis-api.md) |
| Nest → Graph Engine | [graph-contract.md](./graph-contract.md) |
| Edge directions in the topology | [topology-mapping.md](./topology-mapping.md) |

## Which analysis when?

| Need | Analysis id | Seeds? |
|------|-------------|--------|
| “If this node fails, what else breaks?” | `blast_radius` | **Yes** — the failed node(s) |
| “If this node is compromised, what can an attacker reach next?” | `attack_paths` | **Yes** — the compromised node(s) |
| “What are the most critical nodes in this topology?” | `critical_nodes` | **No** |

You can request several analyses in one call.

## Frontend → Nest

The browser does **not** call the Graph Engine. It calls Nest:

`POST /api/v1/environments/{environmentId}/analyses`

Seeds are top-level `seedNodeIds` (not under `options`). Full request/response, limits, and errors: [analysis-api.md](./analysis-api.md).

Nest answers with engine `results` plus **freshness** (`current` / `stale` / `unverifiable`). Show `stale` and `unverifiable` as such — never treat an empty result as “no risk” unless `freshness` is `current`.

## Nest → Graph Engine

Nest sends the current topology snapshot to `POST /api/v1/analyze` (`GRAPH_ENGINE_URL`). Contract details: [graph-contract.md](./graph-contract.md).

| Analysis id | Result key under `data.results` |
|-------------|-----------------------------------|
| `blast_radius` | `blastRadius` |
| `attack_paths` | `attackPaths` |
| `critical_nodes` | `criticalNodes` |

### Seeds (engine options)

- Required for `blast_radius` and `attack_paths` (`options.seedNodeIds` on the engine request; Nest maps its top-level field).
- Must exist in the topology.
- `critical_nodes` ignores seeds.
- **Several seeds:**
  - `blast_radius` — union of impact cones.
  - `attack_paths` — seeds are walked **in order**; `maxPaths` (default 20) is a **shared** cap for the whole request. An early seed can use up the budget so a later seed gets no paths.

### Traversal (today)

Walk direction and which edge kinds each analysis uses: [graph-contract.md](./graph-contract.md) (analysis direction / edge kinds). Do not duplicate that table here.

Quick orientation:

- `blast_radius` — reversed walk; `affectedNodeIds` **includes the seed** at depth `0` (e.g. seed `db` → list starts with `db`, then dependents).
- `attack_paths` — forward walk from the seed(s).
- `critical_nodes` — ranking / scoring, not a walk; formula and reason codes in [graph-contract.md](./graph-contract.md).

Example with `api --depends_on--> db`:

- Seed `db` + `blast_radius` → `db`, then nodes that depend on it (e.g. `api`, then `worker` if `worker → api`).
- Seed `api` + `attack_paths` → paths such as `api → db`.

## Stable examples

Checked-in topologies and golden analyze responses: `services/graph-engine/fixtures/` (Graph Engine README). Useful for Nest tests without inventing shapes.
