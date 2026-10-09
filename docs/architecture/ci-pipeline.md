# GitHub Actions CI Pipeline

## Overview

The `.github/workflows/ci.yml` workflow validates pull requests targeting `main`, commits pushed to `main`, and manual runs (`workflow_dispatch`). It does **not** deploy the application or start the Docker Compose stack.

The workflow uses five independent jobs. A failure in one job does not prevent the other jobs from reporting their results.

| Job | Checks |
|---|---|
| Frontend | `npm ci`, lint, production build |
| Backend | `npm ci`, lint, existing unit test command, Nest build (including Prisma client generation) |
| Collector | `npm ci`, typecheck, Vitest coverage thresholds, TypeScript build |
| Graph Engine | Python dependencies, pytest |
| Docker Compose | Static `docker compose config --quiet` validation with dummy database variables |

Node services run with Node.js 22; Graph Engine runs with Python 3.12. Each job installs its own dependencies and caches package downloads using the matching lock/requirements file. On a PR, a superseded in-progress CI run is cancelled.

## Running checks locally

From the repository root:

```bash
npm --prefix frontend ci
npm --prefix frontend run lint
npm --prefix frontend run build

npm --prefix backend ci
npm --prefix backend run lint
npm --prefix backend run test
npm --prefix backend run build

npm --prefix services/collector ci
npm --prefix services/collector run typecheck
npm --prefix services/collector run test:cov
npm --prefix services/collector run build

(
  cd services/graph-engine
  python3 -m pip install -r requirements.txt
  python3 -m pytest -q
)

POSTGRES_USER=transcendence_ci \
POSTGRES_PASSWORD=placeholder_for_ci_only \
POSTGRES_DB=transcendence_ci \
  docker compose -f compose.yml config --quiet
```

## How to interpret a CI result

- A failed job should be investigated before a PR is merged. Open the job's step logs in the GitHub Actions tab.
- Backend `npm run test` currently uses `vitest run --passWithNoTests`. This means a green backend test step **does not establish that any tests ran**. Backend test coverage is a separate owner follow-up.
- Collector coverage thresholds are enforced by `services/collector/vitest.config.ts`.
- Docker Compose validation checks only the configuration syntax and interpolation; it is not a running integration test.
- This first version does not include an `npm audit` blocking gate: known pre-existing vulnerabilities must be evaluated by the respective frontend/backend owners separately.
- CI is not a production deployment or a substitute for browser/API/realtime integration tests.

## Security and repository governance

- Uses a read-only GitHub Actions token (`contents: read`).
- Runs on `pull_request`, not `pull_request_target`.
- Does not need application secrets or the `PROJECT_TOKEN` used by the separate Project Board workflow.
- Never calls `docker compose up` and never exposes the Docker Engine socket.
- Required status checks in the `main` branch ruleset should be enabled **after** the workflow produces stable green checks and the team agrees on the rollout.
