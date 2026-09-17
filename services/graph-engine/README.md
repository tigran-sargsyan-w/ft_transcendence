# Graph Engine

Stateless Python service for infrastructure graph analysis (blast radius, attack paths, critical nodes).

See `docs/architecture/graph-contract.md` and `docs/adr/0001-python-graph-engine.md`.

## Requirements

- Python 3.11+

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
