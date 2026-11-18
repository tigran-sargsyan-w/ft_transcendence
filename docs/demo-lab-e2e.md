# Demo Lab — end-to-end acceptance evidence

**Date:** 18 November 2026  
**Branch:** `feat/141-demo-lab`  
**Related issues:** [#141](https://github.com/tigran-sargsyan-w/ft_transcendence/issues/141), [#144](https://github.com/tigran-sargsyan-w/ft_transcendence/issues/144)  
**Environment:** project's Ubuntu 26.04 development VM, existing Vault-backed application stack, one Docker Engine.

## Purpose and evidence level

Record actual terminal-observed results of the disposable Demo Lab and authenticated multi-user realtime topology. Results were produced by the operator using the real Docker Engine, Collector, NestJS, Socket.IO, and Graph Engine; they are **manual E2E evidence**, not automated browser tests or continuous CI coverage. Test accounts used development credentials; no passwords, session cookies, or private keys are recorded here.

## Topology and safety

Baseline: `web -> api -> db` (`depends_on`). Optional `worker -> api` is added only for discovery testing. The lab is the isolated `ft-demo-lab` Compose project: no published ports, real database, Docker socket, host bind mounts, or persistent volumes. Its network is internal. The main application is observed on the same Docker Engine but is not modified. The Collector uses the existing read-only Docker API proxy. `make health` reported `RESULT: DEVELOPMENT HEALTHY (read-only)` before and after tested lab operations.

## Automated checks

Executed against Demo Lab v1.2.1 on the operator's VM:

```bash
(cd demo-lab && python3 -m unittest -v test_server test_commands)
node --test backend/scripts/demo-watch-format.test.mjs
bash -n demo-lab/demo.sh
git diff --check
```

| Check | Result |
|---|---|
| Python HTTP behavior + safe command invocation | PASS — 16/16 |
| Node.js analysis/edge formatting | PASS — 6/6 |
| Bash syntax | PASS |
| Git whitespace check | PASS |
| Core app readiness (`make health`) | PASS |

`test_server.py` emitted a non-fatal Python `ResourceWarning` caused by a test HTTPError cleanup path. This should be tidied in a follow-up; it did not fail the acceptance run.

## Manual Demo Lab E2E scenarios

| Scenario | Operation | Actual observed product behavior | Result |
|---|---|---|---|
| A — API degradation | `fail api` / `recover api` | API and Web became `degraded`; DB stayed healthy; recovery emitted `healthy` transitions | PASS |
| B — DB cascade | `fail db` / `recover db` | DB, API, Web degraded; structural `blast_radius` reported DB depth 0, API depth 1, Web depth 2; all recovered | PASS |
| C — Hard outage | `stop api` / `start api` | `r131` API `healthy -> down`, `r132` Web `healthy -> degraded`; `r133–r135` API and Web recovered | PASS |
| D — Dynamic discovery | `add worker`, fail/recover API, `remove worker` | `r148` new Worker and `DEPENDENCY added: worker -> api`; `r149` healthy; API blast radius at `r150` included API, Web and Worker; `r151` Web and Worker degraded; `r152–r153` recovered; `r154–r155` Worker and dependency removed | PASS |
| E — Container recreation | `recreate api` | Physical Docker API ID changed `605907b3… -> 9d471712…`, logical node remained `svc:ft-demo-lab:api`; `api -> db` Docker dependency label survived; `r156–r158` showed down/unknown/healthy; DB was not recreated | PASS |

### Dynamic-relationship regression discovered and fixed

Initially `add worker` used Compose `up --no-deps`, and the Docker label `com.docker.compose.depends_on` was empty. The worker's HTTP health still depended on API, but Collector could not publish a `worker -> api` graph edge, so Graph Engine reported only API and Web as potentially affected.

v1.2.1 switched to `--no-recreate` for dependency-preserving operations and checks the Worker dependency label. On the operator's VM, `docker inspect` returned `api:service_healthy:false` for Worker, and the Socket.IO stream showed `DEPENDENCY added: worker -> api`. The API blast radius expanded to include Worker (depth 1), and the reported API critical-node score changed from 0.25 to 0.33. Following Worker removal, the edge disappeared and the score returned to 0.25.

## Multi-user E2E — two distinct authenticated clients

**Setup:** Two separate CLI processes on the **same physical VM**, each logging in with a separate development user account through `POST /api/v1/auth/login` and using a separate `sid` cookie on Socket.IO. Both subscribed to `env_local_compose`. No direct database writes or simulated Socket.IO messages were used.

**Commands:**

```bash
# Terminal 1
./demo-lab/demo.sh up

# Terminal 2 — User A (existing dedicated test account)
node backend/scripts/demo-watch.mjs --email demo-tester@example.com

# Terminal 3 — User B (independent dedicated test account)
node backend/scripts/demo-watch.mjs --email demo-evaluator-2@example.com

# Once both clients are connected, Terminal 1
./demo-lab/demo.sh fail db
./demo-lab/demo.sh recover db
```

**Observed broadcast evidence:**

| Revision | State transition | User A | User B |
|---|---|---|---|
| r196 | Initial baseline snapshot, DB/API/Web healthy | Received | Received |
| r197 | Web healthy -> degraded | Received | Received |
| r198 | DB healthy -> degraded | Received | Received |
| r199 | API healthy -> degraded | Received | Received |
| r200 | API degraded -> healthy | Received | Received |
| r201 | Web degraded -> healthy | Received | Received |
| r202 | DB degraded -> healthy | Received | Received |

The two clients received the same topology revisions in the same order. Healthcheck discovery order is asynchronous and does not necessarily represent the causal order of failure.

### Manual disconnect and reconnect

1. At **21:59:40**, User B exited the watcher with `Ctrl+C`; User A remained connected.
2. At **21:59:57**, User A received `r203` API healthy -> degraded and `r204` Web healthy -> degraded after an API health failure.
3. At **22:00:30**, User B started a **new authenticated CLI process**, received snapshot revision **r204** and saw API/Web degraded and DB healthy, despite having missed `r203–r204`.
4. After API recovery, **both** clients received `r205` API degraded -> healthy and `r206` Web degraded -> healthy.
5. Final Demo Lab status: DB, API, Web all `healthy`.

**Result:** PASS for simultaneous subscriptions, independent sessions/connections, same-event broadcast, current-state snapshot after manual reconnect, and subsequent same-revision recovery events.

## Reproduction and cleanup

```bash
make health
./demo-lab/demo.sh up
./demo-lab/demo.sh status
# Start one or two demo-watch.mjs clients as described above.
./demo-lab/demo.sh fail db
# Wait until Docker and both observers show degraded transitions.
./demo-lab/demo.sh recover db
# Wait for healthy transitions.
./demo-lab/demo.sh down
./demo-lab/demo.sh status
make health
```

Stop watcher terminals via `Ctrl+C`. Run `make health` to verify the trusted application was not disturbed. Do **not** run `make down` for Demo Lab cleanup.

## Limits and remaining validation

- The multi-user proof used **two terminals on one machine**. It does not prove LAN access from two physical devices or trusted TLS on evaluator laptops.
- Manual `Ctrl+C` followed by a new login does **not** prove automatic reconnection after a network interruption, expired-session handling, or server-driven session revocation.
- Broadcast of topology is **not** the same as concurrent editing of Incident Room records. Multi-user incident workflow and conflict handling require separate testing.
- No frontend/browser UI evaluation was performed; integration with the Dashboard and Incident Room remains a separate team dependency.
- `blast_radius` expresses possible impact from the current dependency graph, not verified root cause. `db` is an HTTP mock, not the real PostgreSQL server.
- Other than the listed unit/offline checks, these are operator-observed manual runs; rerun acceptance after changes to Collector, topology, Graph Engine, Socket.IO or the trusted startup.
