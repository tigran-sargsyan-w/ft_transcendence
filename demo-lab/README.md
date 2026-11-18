# Demo Lab v1 — isolated fault simulation for ft_transcendence

**Scope:** functional demo, not a separately claimed 42 module. All containers are disposable. **Never inject failures into Vault, application PostgreSQL, backend, Collector, Docker proxy, or other core services.**

## Design

A separate, *internal-network-only* Compose project (`ft-demo-lab`) runs three lightweight Python HTTP services:

```text
web  ──depends_on──>  api  ──depends_on──>  db (mock, no real database)
  \__/health calls ___/  \__ /health calls _/
```

Each container checks its own `/run/demo/fail` flag, then the declared upstream `/health`. The Docker healthcheck invokes `/health` every 3 seconds. Setting the flag makes the service unhealthy; an upstream failure propagates through `api` and `web`. The Nest topology maps Docker `unhealthy` to **`degraded`**, not `down`.

No host ports, privileged modes, persistent volumes, external networks or Docker socket mounts. The existing Collector's *read-only* Docker API observes these new containers because it watches the same Docker Engine. Nest currently assigns the **same** `env_local_compose` environment to all containers on that Engine; the demo is a separate Compose project, **not** a separate Nest environment.

## Prerequisites

- Existing Vault-backed application stack already operational on its trusted Ubuntu development VM; do **not** modify or redeploy the core stack just for Demo Lab.
- Docker Engine and modern `docker compose` with `--wait` support.
- For the terminal listener: Node 22, installed backend dev dependencies (`cd backend && npm ci` if they are not already present), an existing application account, and locally trusted `https://transcendence.test:8443` certificate. Do not disable TLS verification.
- Run from the repository root, after copying this `demo-lab/` directory and `backend/scripts/demo-watch.mjs` into their corresponding paths. This package is a proposed addition; it is not installed in the repository automatically.

## Quick start

Terminal A — start the independent lab:

```bash
./demo-lab/demo.sh up
./demo-lab/demo.sh status
```

Terminal B — watch Nest's authenticated WebSocket stream:

```bash
node backend/scripts/demo-watch.mjs --analyze
```

`demo-watch` securely prompts for an existing demo-account email and password, logs in over HTTPS, then subscribes to `topology:subscribe` and prints only nodes with ids `svc:ft-demo-lab:*`. The `--analyze` flag requests a real `blast_radius` + `critical_nodes` analysis when a lab service becomes `degraded`/`down`; omit it to watch topology only. It **does not** auto-create incidents. Do not share account credentials, session cookies or authentication logs.

Terminal C — run a real controlled failure and recovery:

```bash
./demo-lab/demo.sh fail api
./demo-lab/demo.sh status
./demo-lab/demo.sh recover api
./demo-lab/demo.sh status
```

Expected: `api` becomes `degraded`, as does dependent `web`; `db` stays `healthy`. For a longer cascade, run `./demo-lab/demo.sh fail db` then `recover db`. Docker health transitions may take multiple healthcheck intervals; wait for status changes before concluding that a case failed.

Other operations:

```bash
./demo-lab/demo.sh reset  # clear flags on all lab services
./demo-lab/demo.sh logs   # follow only demo container logs
./demo-lab/demo.sh down   # remove only this Compose project's containers + network; no -v
```

Note: the lab may appear alongside the project's own services in topology since the Collector currently inventories the whole Docker Engine.

## Expected real-time events

The backend socket contract uses a `{event, payload, timestamp}` envelope. On connect, the listener gets `topology:snapshot`, then `topology:status`; on health changes it receives `topology:delta` with `node.upsert` operations. The demo node IDs are `svc:ft-demo-lab:db`, `svc:ft-demo-lab:api`, and `svc:ft-demo-lab:web` because Nest uses the Docker Compose project/service labels.

A dependency relation in the graph is *structural*. A `blast_radius` result is an impact **analysis**, not proof that the engine actually detected a real network failure. The Docker healthcheck, Collector event, and `degraded` topology node are the real measured states. The `Incident Room` currently needs a separate manual REST operation; automatic incident creation is not part of this prototype.

## Safety and verification

Unit and syntax checks without Docker:

```bash
cd demo-lab && python3 -m unittest -v test_server && cd ..
bash -n demo-lab/demo.sh
node --check backend/scripts/demo-watch.mjs
```

Live E2E acceptance still needs to run on the user's VM, since a running Docker Engine, Vault-backed Nest backend and authenticated Socket.IO are required. At minimum verify:

1. `status` reports 3 healthy lab containers after `up`.
2. The socket client receives `topology:snapshot` with 3 `svc:ft-demo-lab:*` nodes.
3. `fail api` produces `degraded` for API and, after propagation, web; DB remains healthy.
4. `recover api` produces healthy nodes again.
5. `--analyze` returns `freshness`, `topologyRevision`, `blastRadius` and `criticalNodes`, or a clearly logged API error for follow-up.
6. `down` removes demo project resources but does not affect core app/Vault/PostgreSQL containers or volumes.

Avoid reading or printing Docker environment variables, Vault secret paths, passwords or database connection strings in test output.

## Known limitations

- The demo is **one Docker Engine, one Nest environment**. It is not per-user or multi-environment isolation.
- The `db` service is an HTTP mock that demonstrates dependence, not a real DB.
- This is no automatic incident detection, persistent replay, or web UI.
- The proposed CLI works only against a healthy, reachable backend with an existing user session or credentials.
- There may be compatibility/integration bugs that cannot be found until the complete VM stack is exercised.
