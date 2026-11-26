# Demo Lab v1.2.1 — reproducible multi-scenario infrastructure demonstration

**Issue:** [#141](https://github.com/tigran-sargsyan-w/ft_transcendence/issues/141) · **Purpose:** a functional, terminal-first demonstration of the existing Collector → NestJS → Socket.IO → Graph Engine pipeline, without waiting for the frontend.

Demo Lab is a disposable workload, **not** a new 42 module or a separate monitoring environment. Never inject faults into the actual application, Vault, PostgreSQL, Nginx, Collector or Docker proxy.

## Architecture and scope

A dedicated Compose project (`ft-demo-lab`) runs three baseline Python HTTP mock services plus an optional `worker` that can be added and removed at runtime:

```text
web    ──depends_on──>  api  ──depends_on──>  db
worker ──depends_on──>  api    (optional, discovery profile)
```

Each service exposes `/health` inside its private Docker network. A local `/run/demo/fail` flag makes that service answer HTTP 503. Its dependents also become unhealthy if their upstream `/health` fails. Docker healthchecks run every three seconds (subject to retries), and the existing Collector notices Docker health changes. Nest exposes the service states and dependency edges through its authenticated Socket.IO topology stream. A `blast_radius` query estimates possible downstream impact; it is **not** itself a measurement of causality or traffic.

Safety properties: separate Compose project and internal-only network; no published host ports, Docker socket, host bind mounts, persistent volumes, real database, application secrets, privileged containers or changes to the root `compose.yml` / Vault startup. `down` targets **only** `ft-demo-lab`, with no `-v`.

**Isolation limitation:** The Collector inventories the *whole Docker Engine*, so Demo Lab and the real application belong to one Nest environment, `env_local_compose`. The CLI filters what it prints; it does not partition backend topology or Graph Engine computation.

### How the lab connects to the product (without a network connection between them)

```text
Same Docker host
  ft-demo-lab Compose project --\
                                > Docker Engine API
  main application project ----/       |
                                      v
                            Docker Socket Proxy (read-only)
                                      |
                                      v
                         Collector (snapshot + event stream)
                                      |
                                      v
                              NestJS topology state
                                   /        \
                                  v          v
                         Graph Engine     Socket.IO -> demo-watch.mjs
                         (HTTP analysis)   (over authenticated HTTPS)
```

The lab **never sends events to the product itself**; its containers do not need access to the main application's private networks. Docker healthcheck results and container lifecycle events are observed by the existing collector through the read-only Docker API. Nest associates nodes with their Compose project/service labels. This design currently supports **one Docker Engine**; monitoring separate remote hosts requires an additional collector and backend multi-environment support, which is **not implemented** by Demo Lab.

## Prerequisites

- Existing trusted Vault-backed development installation is healthy: `make health`.
- Docker and Docker Compose with `up --wait`; Node.js 22 and backend dependencies for the watcher (`socket.io-client` is already declared by the backend).
- Valid HTTPS trust for the configured application origin (`python3 -m scripts.dev.configuration origin`); never bypass certificate validation.
- A **test** account in the application's development database; see next section. Do not use production credentials.

Commands below run from the repository root. After extracting a ZIP, if necessary run `chmod +x demo-lab/demo.sh` once.

### Makefile shortcuts (optional)

The root `Makefile` also provides **lab-only** aliases. They call the same `demo-lab/demo.sh` commands; there is no second Docker lifecycle implementation. The **unprefixed** `make up`, `make down`, `make clean`, and `make health` retain their original application/bootstrap meanings.

| Direct command | Make alias |
|---|---|
| `./demo-lab/demo.sh up` | `make demo-up` |
| `./demo-lab/demo.sh status` | `make demo-status` |
| `./demo-lab/demo.sh fail db` | `make demo-fail SERVICE=db` |
| `./demo-lab/demo.sh recover db` | `make demo-recover SERVICE=db` |
| `./demo-lab/demo.sh stop api` | `make demo-stop SERVICE=api` |
| `./demo-lab/demo.sh start api` | `make demo-start SERVICE=api` |
| `./demo-lab/demo.sh recreate api` | `make demo-recreate SERVICE=api` |
| `./demo-lab/demo.sh add worker` | `make demo-add-worker` |
| `./demo-lab/demo.sh remove worker` | `make demo-remove-worker` |
| `./demo-lab/demo.sh reset` | `make demo-reset` |
| `./demo-lab/demo.sh logs` | `make demo-logs` |
| `./demo-lab/demo.sh down` | `make demo-down` |
| `./demo-lab/demo.sh help` | `make demo-help` |

`SERVICE` defaults to `api` for fail/recover/stop/start/recreate; only `db`, `api`, `web`, and `worker` are accepted by `demo.sh`. `make demo-watch` starts the existing authenticated observer with Graph Engine analysis; `make demo-watch-plain` omits automatic analysis (useful for multi-user broadcast testing). Both accept optional `EMAIL`, e.g. `make demo-watch-plain EMAIL=demo-tester@example.com`; the password is always requested interactively. `make demo-test` runs offline Python/Node/Bash checks and does not launch Docker workloads.


## Creating a test account without the frontend

If `/register` has not yet been implemented in React, the existing backend supports `POST /api/v1/auth/register`. This one-time terminal command uses the normal application API and prompts for the password without echoing it or storing it in shell history:

```bash
python3 - <<'PY'
import getpass
import json
import urllib.request
from scripts.dev.configuration import load

email = input('Unique demo email: ').strip()
password = getpass.getpass('Demo password (min. 8 characters): ')
request = urllib.request.Request(
    load().app_origin + '/api/v1/auth/register',
    data=json.dumps({'email': email, 'password': password, 'displayName': 'Demo Tester'}).encode(),
    headers={'Content-Type': 'application/json'},
    method='POST',
)
with urllib.request.urlopen(request, timeout=15) as response:
    print('Registration HTTP:', response.status)
PY
```

Expected status is `201`. A `409` means that email is already registered: use the existing account or choose a different email. The account is stored in the development PostgreSQL database, not in the disposable Demo Lab.

## Quick start

**Terminal A** — start lab containers and inspect real Docker health:

```bash
make health
./demo-lab/demo.sh up
./demo-lab/demo.sh status
```

Expected: `db`, `api`, and `web` all eventually report `healthy`.

**Terminal B** — observe the actual authenticated backend topology and analyses:

```bash
node backend/scripts/demo-watch.mjs --analyze
```

The watcher prompts for demo-account email and password, logs in over HTTPS, connects to Socket.IO using the session cookie, then prints only Demo Lab service nodes by default. `--analyze` requests the backend's real `blast_radius` + `critical_nodes` analyses on `degraded`/`down` transitions. Adding/removing `worker` also prints its real `depends_on` edge update; no analysis is automatically triggered for a newly healthy node.

For a concise summary (default):

```text
DELTA r74: svc:ft-demo-lab:db healthy -> degraded
ANALYSIS seed=db freshness=current revision=...:74
  BLAST potential impact: db(depth 0), api(depth 1), web(depth 2)
  CRITICAL lab scores: db=0.40, api=0.25, web=0.00
```

Scores and revisions depend on the live topology. `critical_nodes` includes nodes outside the lab at the API level; the default terminal view only prints lab scores. The complete API result is never modified.

Watcher flags:

| Flag | Purpose |
|------|---------|
| `--analyze` | Request real Graph Engine analyses on failing demo nodes |
| `--verbose` | **With `--analyze`**, print the complete raw analysis JSON after the compact summary |
| `--all` | Show topology changes for *all* Docker-host nodes (not just the lab) |
| `--email address` | Skip only the email prompt (password remains hidden and interactive) |
| `--url https://host:port` | Choose a trusted HTTPS backend origin |
| `--help` | Show watcher usage |

A watcher session permits at most **12 analysis requests** to prevent accidental request flooding; topology observation continues afterward. Restart the watcher for a new test session. `--verbose` may print infrastructure node IDs and scores: do not post full output or credentials publicly.

**Terminal C** — run one of the scenarios below. Keep the watcher running so you can observe each transition.

## Five distinct demonstration scenarios

These illustrate **different product capabilities**, not five kinds of the same error. All five scenarios A–E were run and verified on the operator's real Docker Engine on 18 November 2026. See [Demo Lab E2E acceptance evidence](../docs/demo-lab-e2e.md) for exact revisions, expected observations, and limitations.

### Scenario A — Service degradation (API health failure)

Shows `healthy -> degraded`, actual Docker healthchecks, real-time topology updates, and structural blast radius. DB stays healthy while API and Web degrade:


```bash
./demo-lab/demo.sh fail api
./demo-lab/demo.sh status
# Wait until Docker and the watcher observe api + web as unhealthy / degraded.
./demo-lab/demo.sh recover api
./demo-lab/demo.sh status
```

### Scenario B — Cascading dependency failure (DB)

Shows a two-hop blast radius: DB (depth 0), API (depth 1), Web (depth 2). Dependents really fail their healthchecks because their upstream does not answer healthy:


```bash
./demo-lab/demo.sh fail db
# Wait for db, api, web to become unhealthy and for the analysis to appear.
./demo-lab/demo.sh recover db
# Wait for all three to become healthy again.
```

### Scenario C — Hard outage vs degradation (container stop/start)

A stopped container is `down`, which is distinct from an unhealthy but running container (`degraded`). The Web dependency should also degrade while API is stopped. The DB should remain healthy.

```bash
./demo-lab/demo.sh stop api
./demo-lab/demo.sh status
# Observe API -> down and Web -> degraded in the watcher.
./demo-lab/demo.sh start api
./demo-lab/demo.sh status
# Observe API and Web eventually become healthy again.
```

The service must exist before `start`; this is not a `docker compose up` replacement. Restarting a stopped lab container does not change its logical service ID.

### Scenario D — Dynamic infrastructure discovery / dependency edge

The optional `worker` does **not exist** when the lab is first started. Adding it creates a real fourth container and a new `worker -> api` dependency. The watcher prints the new service node and `DEPENDENCY added` without any restart. Removing it deletes the node and dependency edge.

**Compose metadata requirement:** `add worker` must not use `up --no-deps`. On some Docker Compose versions that option empties the `com.docker.compose.depends_on` label, so the worker still fails when API fails but the Collector/Graph Engine cannot see the dependency. The command uses `--no-recreate` to preserve existing lab services and fails if the worker's Docker label does not include API. If upgrading from the affected v1.2 script **while a worker is already running**, first run `./demo-lab/demo.sh remove worker`, then use `add worker` with the updated script.

```bash
./demo-lab/demo.sh add worker
./demo-lab/demo.sh status
# Observe worker new -> unknown -> healthy and DEPENDENCY added.
./demo-lab/demo.sh remove worker
./demo-lab/demo.sh status
# Observe DEPENDENCY removed and worker removed.
```

You can also inject an API failure **while the worker is present** to show how the Graph Engine's potential impact changes from API + Web to API + Web + Worker. Recover API before removing Worker. Worker is an HTTP mock, not a job queue or real data pipeline.

### Scenario E — Container replacement with stable service identity

The script stops/removes **only the named disposable lab container**, then creates it again with Compose dependency metadata intact. It uses `up --no-recreate` so already-running lab dependencies are not recreated. The physical container ID changes while Nest's logical service ID remains `svc:ft-demo-lab:api`; depending on event timing the logical node may briefly disappear and reappear. No trusted application services are touched.

```bash
BEFORE="$(docker compose -p ft-demo-lab -f demo-lab/compose.yml ps -q api)"
./demo-lab/demo.sh recreate api
AFTER="$(docker compose -p ft-demo-lab -f demo-lab/compose.yml ps -q api)"
printf 'Container before: %s\nContainer after:  %s\n' "$BEFORE" "$AFTER"
./demo-lab/demo.sh status
# Physical IDs should differ; logical service ID remains unchanged.
```

Do not treat a lack of topology delta as a monitoring failure here: rebuilding an identical healthy service can produce an unchanged logical graph. This illustrates service identity normalization, not guaranteed zero downtime.

### Clean baseline and safety

```bash
./demo-lab/demo.sh reset    # starts stopped baseline containers, clears flags, removes optional worker
./demo-lab/demo.sh status   # wait for db/api/web healthy
./demo-lab/demo.sh down     # removes only the lab containers and its private network
make health                 # confirms trusted app remains healthy
```

**Important:** `fail` and `recover` change the mock service immediately, but Docker and Nest only report the resulting state after asynchronous healthcheck/event processing. An immediate `status` can still show `healthy` *after fail* or `unhealthy` *after recover*. It is the eventual transition that confirms the scenario.

Other lab-only operations:

```bash
./demo-lab/demo.sh reset   # clear every injected failure flag
./demo-lab/demo.sh logs    # follow logs of lab containers only
./demo-lab/demo.sh down    # remove disposable lab containers + network; no -v
./demo-lab/demo.sh up      # clean restart and re-discovery
make health                # ensure core app remained healthy
```

During `down` the watcher may briefly report `down` before `node.remove`; this is expected. During re-creation it may show `new -> unknown -> healthy`. The watcher can also request a blast-radius analysis during intentional teardown; it does not mean the core app is failing.

## Manual acceptance evidence — 18 November 2026 (v1 / v1.1)

The following scenarios were **run on the project's actual Ubuntu development VM** and output was supplied by the project operator. The v1.1 compact CLI was also run successfully: its 17:05 test confirmed a three-hop DB cascade and recovery with short analysis summaries. Later runs validated all v1.2.1 scenarios and multi-user CLI topology broadcast. See [consolidated E2E acceptance report](../docs/demo-lab-e2e.md).

| Scenario | Observed result | Result |
|----------|-----------------|--------|
| Base readiness | `make health`: `DEVELOPMENT HEALTHY`; three lab services `healthy` | PASS |
| WebSocket discovery | Initial `topology:snapshot` contained all three `svc:ft-demo-lab:*` nodes and both `depends_on` edges | PASS |
| API failure | API + web became Docker `unhealthy` / Nest `degraded`; DB remained healthy; `blast_radius(api)` included API + web | PASS |
| API recovery | Socket.IO revisions `r65`, `r66` returned API + web to `healthy` | PASS |
| DB cascade | `r74` DB degraded; `r75` API + web degraded; `blast_radius(db)` returned depths 0/1/2 | PASS |
| DB recovery | `r76`, `r77` returned all three services to `healthy` | PASS |
| Safe teardown | `down` removed only the three lab containers and its network; Nest removed lab nodes by `r83` | PASS |
| Clean lab restart | Nodes reappeared (`r84–r86`) and returned to `healthy` (`r87–r89`) without restarting the backend or Collector | PASS |
| After restart | `make health`: `DEVELOPMENT HEALTHY` | PASS |

See the live terminal transcript or PR test notes for details. A single passing manual run establishes the observed behavior, not ongoing automated E2E coverage.

### E2E acceptance of new scenarios and multi-user observation (v1.2.1)

**PASS (18 November 2026, operator VM):** `stop/start api` distinguished `down` from `degraded`; `add/remove worker` created/removed both its node and `worker -> api` dependency; API blast radius expanded dynamically; `recreate api` changed the physical Docker ID but retained `svc:ft-demo-lab:api` and its `api -> db` label. Two independently authenticated CLI clients also received identical topology revisions, and a manually reconnected client received the current snapshot. Detailed evidence, pass/fail results, and untested boundaries are recorded in [docs/demo-lab-e2e.md](../docs/demo-lab-e2e.md).

## Offline checks

```bash
(cd demo-lab && python3 -m unittest -v test_server test_commands)
bash -n demo-lab/demo.sh
node --check backend/scripts/demo-watch.mjs
node --check backend/scripts/demo-watch-format.mjs
node --test backend/scripts/demo-watch-format.test.mjs
```

## Known limitations and non-goals

- `db` is a lightweight HTTP mock, **not** a real database failure or a test of Prisma/PostgreSQL recovery. The optional `worker` is also a simple HTTP mock.
- The Graph Engine's blast radius is structural, not a verified root-cause diagnosis. Actual unhealthy states come from Docker/Collector.
- No automatic Incident Room creation, persistent incident replay, Prometheus alerts, or frontend UI is implemented by this lab.
- Demo Lab shares the Docker host's topology environment with the main app; only its Compose/network resources are isolated.
- The watcher requires an authenticated dev account, trusted HTTPS and backend `socket.io-client` dependencies.
- The replacement scenario shows stable Compose service IDs, but node metadata does not expose replica counts or individual container IDs. A healthy replica can hide another failed replica in the current status mapping.
- The scenario scripts do not generate real cyberattacks, CPU/memory telemetry, Prometheus alerts, or automated incident records.
- Before an evaluation, rerun the relevant tests after changing the Collector, topology mapping, Graph Engine or backend startup behavior.
