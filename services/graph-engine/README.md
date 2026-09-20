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

## Analyze (stub)

`POST /api/v1/analyze` validates the graph-contract request and returns empty result shells.
Real algorithms are not implemented yet.

```bash
curl -s http://localhost:8000/api/v1/analyze \
  -H 'Content-Type: application/json' \
  -d '{
    "topology": {
      "schemaVersion": 1,
      "environmentId": "env_local_compose",
      "capturedAt": "2026-09-14T10:00:00Z",
      "nodes": [
        {"id": "svc_api", "kind": "service", "label": "api", "status": "down"},
        {"id": "svc_db", "kind": "service", "label": "db", "status": "healthy"}
      ],
      "edges": [
        {"id": "edge_api_db", "source": "svc_api", "target": "svc_db", "kind": "depends_on"}
      ]
    },
    "analyses": ["blast_radius"],
    "options": {"seedNodeIds": ["svc_api"]}
  }'
```
