# Graph Engine

Stateless Python service for infrastructure graph analysis (blast radius, attack paths, critical nodes).

See `docs/architecture/graph-contract.md`, the Nest/frontend [analyses cheat-sheet](../../docs/architecture/graph-engine-analyses.md), and `docs/adr/0001-python-graph-engine.md`.

## Requirements

- Python 3.9+

## Local setup (venv)

```bash
cd services/graph-engine
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

## Docker Compose

From the repo root:

```bash
docker compose up -d --build graph-engine
curl http://localhost:8000/api/v1/health
```

In Compose, other services reach the engine at `http://graph-engine:8000`.  
Nest should use env var `GRAPH_ENGINE_URL` (set on the `backend` service in `compose.yml`).  
From the host (Nest or curl outside Compose), use `http://localhost:8000`.

## Health check

```bash
curl http://localhost:8000/api/v1/health
```

Expected shape:

```json
{
  "data": {
    "status": "ok"
  }
}
```

## Analyze

`POST /api/v1/analyze` validates the graph-contract request.

- `blast_radius` — implemented (who depends on the seed, via reversed `depends_on` edges only)
- `attack_paths` — implemented (forward paths from a compromised seed along `depends_on` only)
- `critical_nodes` — implemented (v1 risk-map score: `0.6` betweenness on `depends_on` only + `0.4` transitive dependents; reasons may include `exposed_to_internet`; no seeds required — see `docs/architecture/graph-contract.md`)

## Fixtures (Nest integration)

Topology snapshots live under `fixtures/`. Golden `/analyze` envelopes (stable examples for Nest) live under `fixtures/golden/`.

| File | Purpose |
|------|---------|
| `fixtures/sample_topology.json` | Small `depends_on`-only demo (README / golden happy paths) |
| `fixtures/mixed_kind_topology.json` | Nest-style ids + all edge kinds (`depends_on`, `connects_to`, `mounts`, `runs_on`, `exposes`) |
| `fixtures/cycle_topology.json` | `depends_on` cycle (a → b → c → a) |
| `fixtures/multi_seed_topology.json` | Two independent chains (useful multi-seed blast/attack) |
| `fixtures/golden/sample_blast_radius.json` | Expected blast response for `sample_topology` + seed `svc_db` |
| `fixtures/golden/sample_attack_paths.json` | Expected attack response for `sample_topology` + seed `svc_api` |
| `fixtures/golden/sample_critical_nodes.json` | Expected critical-nodes response for `sample_topology` |

`analyzedAt` in goldens is fixed (`2026-09-14T10:00:01Z`). Live responses use the current UTC time; compare `results` (and ignore or normalize `analyzedAt`).

Edge directions in `mixed_kind_topology.json` follow `docs/architecture/topology-mapping.md`.

### Curl against a fixture

From `services/graph-engine` (engine already running on port 8000). Requires `jq`.

Blast radius (`sample_topology`, seed `svc_db`):

```bash
jq -n --slurpfile t fixtures/sample_topology.json \
  '{topology: $t[0], analyses: ["blast_radius"], options: {seedNodeIds: ["svc_db"]}}' \
  | curl -s http://localhost:8000/api/v1/analyze \
    -H 'Content-Type: application/json' \
    -d @-
```

Attack paths (same topology, seed `svc_api`):

```bash
jq -n --slurpfile t fixtures/sample_topology.json \
  '{topology: $t[0], analyses: ["attack_paths"], options: {seedNodeIds: ["svc_api"]}}' \
  | curl -s http://localhost:8000/api/v1/analyze \
    -H 'Content-Type: application/json' \
    -d @-
```

Critical nodes (no seeds):

```bash
jq -n --slurpfile t fixtures/sample_topology.json \
  '{topology: $t[0], analyses: ["critical_nodes"]}' \
  | curl -s http://localhost:8000/api/v1/analyze \
    -H 'Content-Type: application/json' \
    -d @-
```

Mixed-kind / multi-seed examples (same pattern, change fixture and seeds):

```bash
# multi-seed blast: db + redis
jq -n --slurpfile t fixtures/multi_seed_topology.json \
  '{topology: $t[0], analyses: ["blast_radius"], options: {seedNodeIds: ["svc:demo:db", "svc:demo:redis"]}}' \
  | curl -s http://localhost:8000/api/v1/analyze \
    -H 'Content-Type: application/json' \
    -d @-
```

### Pytest

From `services/graph-engine`:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
PYTHONPATH=. python -m pytest -q
```

`tests/test_golden.py` locks the three sample goldens. CI runs the same suite (`Graph Engine / pytest`).
