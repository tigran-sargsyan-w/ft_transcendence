# Graph Engine

Stateless Python service for infrastructure graph analysis (blast radius, attack paths, critical nodes).

See `docs/architecture/graph-contract.md` and `docs/adr/0001-python-graph-engine.md`.

## Requirements

- Python 3.9+

## Local setup

```bash
cd services/graph-engine
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

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

- `blast_radius` — implemented (who depends on the seed service, via reversed dependency edges)
- `attack_paths` — implemented (forward paths from a compromised seed)
- `critical_nodes` — implemented (betweenness + dependents ranking; no seeds required)

### Blast radius example

Seed = `svc_db` → expects `svc_api` and `svc_worker` in the blast radius:

```bash
curl -s http://localhost:8000/api/v1/analyze \
  -H 'Content-Type: application/json' \
  -d @- <<'EOF'
{
  "topology": {
    "schemaVersion": 1,
    "environmentId": "env_local_compose",
    "capturedAt": "2026-09-14T10:00:00Z",
    "nodes": [
      {"id": "svc_api", "kind": "service", "label": "api", "status": "down"},
      {"id": "svc_worker", "kind": "service", "label": "worker", "status": "healthy"},
      {"id": "svc_db", "kind": "service", "label": "db", "status": "healthy"},
      {"id": "svc_cache", "kind": "service", "label": "cache", "status": "healthy"}
    ],
    "edges": [
      {"id": "edge_api_db", "source": "svc_api", "target": "svc_db", "kind": "depends_on"},
      {"id": "edge_api_cache", "source": "svc_api", "target": "svc_cache", "kind": "depends_on"},
      {"id": "edge_worker_api", "source": "svc_worker", "target": "svc_api", "kind": "depends_on"}
    ]
  },
  "analyses": ["blast_radius"],
  "options": {"seedNodeIds": ["svc_db"]}
}
EOF
```

### Attack paths example

Same topology, seed = `svc_api` → expects paths such as `svc_api → svc_db` and `svc_api → svc_cache`:

```bash
curl -s http://localhost:8000/api/v1/analyze \
  -H 'Content-Type: application/json' \
  -d @- <<'EOF'
{
  "topology": {
    "schemaVersion": 1,
    "environmentId": "env_local_compose",
    "capturedAt": "2026-09-14T10:00:00Z",
    "nodes": [
      {"id": "svc_api", "kind": "service", "label": "api", "status": "down"},
      {"id": "svc_worker", "kind": "service", "label": "worker", "status": "healthy"},
      {"id": "svc_db", "kind": "service", "label": "db", "status": "healthy"},
      {"id": "svc_cache", "kind": "service", "label": "cache", "status": "healthy"}
    ],
    "edges": [
      {"id": "edge_api_db", "source": "svc_api", "target": "svc_db", "kind": "depends_on"},
      {"id": "edge_api_cache", "source": "svc_api", "target": "svc_cache", "kind": "depends_on"},
      {"id": "edge_worker_api", "source": "svc_worker", "target": "svc_api", "kind": "depends_on"}
    ]
  },
  "analyses": ["attack_paths"],
  "options": {"seedNodeIds": ["svc_api"]}
}
EOF
```

### Critical nodes example

Same topology, no seeds → ranked nodes with `score` and `reasons` (often `svc_api` near the top):

```bash
curl -s http://localhost:8000/api/v1/analyze \
  -H 'Content-Type: application/json' \
  -d @- <<'EOF'
{
  "topology": {
    "schemaVersion": 1,
    "environmentId": "env_local_compose",
    "capturedAt": "2026-09-14T10:00:00Z",
    "nodes": [
      {"id": "svc_api", "kind": "service", "label": "api", "status": "down"},
      {"id": "svc_worker", "kind": "service", "label": "worker", "status": "healthy"},
      {"id": "svc_db", "kind": "service", "label": "db", "status": "healthy"},
      {"id": "svc_cache", "kind": "service", "label": "cache", "status": "healthy"}
    ],
    "edges": [
      {"id": "edge_api_db", "source": "svc_api", "target": "svc_db", "kind": "depends_on"},
      {"id": "edge_api_cache", "source": "svc_api", "target": "svc_cache", "kind": "depends_on"},
      {"id": "edge_worker_api", "source": "svc_worker", "target": "svc_api", "kind": "depends_on"}
    ]
  },
  "analyses": ["critical_nodes"]
}
EOF
```

A copy of this topology lives in `fixtures/sample_topology.json`.
