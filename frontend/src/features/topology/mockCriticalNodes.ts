import type { CriticalNodeRow } from "./types";

/**
 * What the critical_nodes analysis returns for mockTopology
 * (services/graph-engine/app/critical_nodes.py), joined with the label and
 * status of each service.
 *
 * score = 0.6 × betweenness + 0.4 × dependents / max dependents, on the
 * depends_on edges only: nginx → frontend, nginx → backend,
 * frontend → backend, backend → postgres, backend → vault.
 * - betweenness: backend is on 4 of the 12 shortest paths (1/3), others 0
 * - dependents: postgres 3, vault 3, backend 2, frontend 1 (max 3)
 *
 * Sorted like the engine: score descending, then id. The engine also returns
 * the host, network, volume and internet nodes (score 0, no reason): left out
 * here, as a row is a service.
 */
export const mockCriticalNodes: CriticalNodeRow[] = [
  {
    id: "svc:transcendence:backend",
    label: "backend",
    status: "degraded",
    score: 0.4667,
    reasons: ["high_betweenness", "many_dependents"],
  },
  {
    // backend, frontend and nginx fail with it
    id: "svc:transcendence:postgres",
    label: "postgres",
    status: "down",
    score: 0.4,
    reasons: ["many_dependents"],
  },
  {
    id: "svc:transcendence:vault",
    label: "vault",
    status: "healthy",
    score: 0.4,
    reasons: ["many_dependents"],
  },
  {
    // Only nginx depends on it: below the many_dependents threshold (1.5)
    id: "svc:transcendence:frontend",
    label: "frontend",
    status: "healthy",
    score: 0.1333,
    reasons: ["structural_role"],
  },
  {
    // Not on any depends_on path; services with no depends_on edge get 0 too
    id: "svc:transcendence:auth",
    label: "auth",
    status: "compromised",
    score: 0,
    reasons: [],
  },
  {
    id: "svc:transcendence:collector",
    label: "collector",
    status: "down",
    score: 0,
    reasons: [],
  },
  {
    id: "svc:transcendence:graph-engine",
    label: "graph-engine",
    status: "healthy",
    score: 0,
    reasons: [],
  },
  {
    // exposed_to_internet is not part of the score: score 0 with a reason
    id: "svc:transcendence:nginx",
    label: "nginx",
    status: "healthy",
    score: 0,
    reasons: ["exposed_to_internet"],
  },
  {
    id: "svc:transcendence:redis",
    label: "redis",
    status: "unknown",
    score: 0,
    reasons: [],
  },
];
