# Demo Lab v1.1 — reproducible infrastructure incident demonstration

**Issue:** [#141](https://github.com/tigran-sargsyan-w/ft_transcendence/issues/141) · **Purpose:** a functional, terminal-first demonstration of the existing Collector → NestJS → Socket.IO → Graph Engine pipeline, without waiting for the frontend.

Demo Lab is a disposable workload, **not** a new 42 module or a separate monitoring environment. Never inject faults into the actual application, Vault, PostgreSQL, Nginx, Collector or Docker proxy.

## Architecture and scope

A dedicated Compose project (`ft-demo-lab`) runs three minimal Python HTTP mock services:

```text
web  ──depends_on──>  api  ──depends_on──>  db
            /health calls   /health calls
```

Each service exposes `/health` inside its private Docker network. A local `/run/demo/fail` flag makes that service answer HTTP 503. Its dependents also become unhealthy if their upstream `/health` fails. Docker healthchecks run every three seconds (subject to retries), and the existing Collector notices Docker health changes. Nest exposes the service states and dependency edges through its authenticated Socket.IO topology stream. A `blast_radius` query estimates possible downstream impact; it is **not** itself a measurement of causality or traffic.

Safety properties: separate Compose project and internal-only network; no published host ports, Docker socket, host bind mounts, persistent volumes, real database, application secrets, privileged containers or changes to the root `compose.yml` / Vault startup. `down` targets **only** `ft-demo-lab`, with no `-v`.

**Isolation limitation:** The Collector inventories the *whole Docker Engine*, so Demo Lab and the real application belong to one Nest environment, `env_local_compose`. The CLI filters what it prints; it does not partition backend topology or Graph Engine computation.

## Prerequisites

- Existing trusted Vault-backed development installation is healthy: `make health`.
- Docker and Docker Compose with `up --wait`; Node.js 22 and backend dependencies for the watcher (`socket.io-client` is already declared by the backend).
- Valid HTTPS trust for `https://transcendence.test:8443` on the host; never bypass certificate validation.
- A **test** account in the application's development database; see next section. Do not use production credentials.

Commands below run from the repository root. After extracting a ZIP, if necessary run `chmod +x demo-lab/demo.sh` once.

## Creating a test account without the frontend

If `/register` has not yet been implemented in React, the existing backend supports `POST /api/v1/auth/register`. This one-time terminal command uses the normal application API and prompts for the password without echoing it or storing it in shell history:

```bash
python3 - <<'PY'
import getpass
import json
import urllib.request

email = input('Unique demo email: ').strip()
password = getpass.getpass('Demo password (min. 8 characters): ')
request = urllib.request.Request(
    'https://transcendence.test:8443/api/v1/auth/register',
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

The watcher prompts for demo-account email and password, logs in over HTTPS, connects to Socket.IO using the session cookie, then prints only Demo Lab service nodes by default. `--analyze` requests the backend's real `blast_radius` + `critical_nodes` analyses on `degraded`/`down` transitions.

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

**Terminal C** — inject and clear a failure:

```bash
./demo-lab/demo.sh fail api
./demo-lab/demo.sh status
# Wait until Docker and the watcher observe api + web as unhealthy / degraded.
./demo-lab/demo.sh recover api
./demo-lab/demo.sh status
```

Or demonstrate the two-hop cascade:

```bash
./demo-lab/demo.sh fail db
# Wait for db, api, web to become unhealthy and for the analysis to appear.
./demo-lab/demo.sh recover db
# Wait for all three to become healthy again.
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

## Manual acceptance evidence — 18 November 2026

The following scenarios were **run on the project's actual Ubuntu development VM** and the output was supplied by the project operator. These results are evidence for v1 runtime behavior, not proof that this updated v1.1 presentation format has been exercised on the VM yet.

| Scenario | Observed result | Result |
|----------|-----------------|--------|
| Base readiness | `make health`: `DEVELOPMENT HEALTHY`; three lab services `healthy` | PASS |
| WebSocket discovery | Initial `topology:snapshot` contained all three `svc:ft-demo-lab:*` nodes and both `depends_on` edges | PASS |
| API failure | API + web became Docker `unhealthy` / Nest `degraded`; DB remained healthy; `blast_radius(api)` included API + web | PASS |
| API recovery | Socket.IO revisions `r65`, `r66` returned API + web to `healthy` | PASS |
| DB cascade | `r74` DB degraded; `r75` API + web degraded; `blast_radius(db)` returned depths 0/1/2 | PASS |
| DB recovery | `r76`, `r77` returned all three services to `healthy` | PASS |
| Safe teardown | `down` removed only the three lab containers and its network; Nest removed lab nodes by `r83` | PASS |
| Recreate | Nodes reappeared (`r84–r86`) and returned to `healthy` (`r87–r89`) without restarting the backend or Collector | PASS |
| After restart | `make health`: `DEVELOPMENT HEALTHY` | PASS |

See the live terminal transcript or PR test notes for details. A single passing manual run establishes the observed behavior, not ongoing automated E2E coverage.

## Offline checks

```bash
(cd demo-lab && python3 -m unittest -v test_server)
bash -n demo-lab/demo.sh
node --check backend/scripts/demo-watch.mjs
node --check backend/scripts/demo-watch-format.mjs
node --test backend/scripts/demo-watch-format.test.mjs
```

## Known limitations and non-goals

- `db` is a lightweight HTTP mock, **not** a real database failure or a test of Prisma/PostgreSQL recovery.
- The Graph Engine's blast radius is structural, not a verified root-cause diagnosis. Actual unhealthy states come from Docker/Collector.
- No automatic Incident Room creation, persistent incident replay, Prometheus alerts, or frontend UI is implemented by this lab.
- Demo Lab shares the Docker host's topology environment with the main app; only its Compose/network resources are isolated.
- The watcher requires an authenticated dev account, trusted HTTPS and backend `socket.io-client` dependencies.
- Before an evaluation, rerun the relevant tests after changing the Collector, topology mapping, Graph Engine or backend startup behavior.
