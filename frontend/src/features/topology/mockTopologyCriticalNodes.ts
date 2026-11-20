import type { CriticalNodeRow } from "./types";

/**
 * Criticality of the services of mockTopology, as the critical_nodes analysis
 * would rank them (POST /api/v1/environments/:environmentId/analyses, see
 * docs/architecture/graph-contract.md), joined with the label and status of
 * the node.
 *
 * score = 0.6 × betweenness + 0.4 × transitive dependents (depends_on only).
 * Sorted like the engine: score descending, then id.
 */
export const mockTopologyCriticalNodes: CriticalNodeRow[] = [
  {
    // On every path from nginx/frontend to postgres and vault; 2 dependents
    id: "svc:transcendence:backend",
    label: "backend",
    status: "degraded",
    score: 0.87,
    reasons: ["high_betweenness", "many_dependents"],
  },
  {
    // backend, frontend and nginx fail with it: the most dependents
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
    // Only nginx depends on it
    id: "svc:transcendence:frontend",
    label: "frontend",
    status: "healthy",
    score: 0.13,
    reasons: ["structural_role"],
  },
  {
    // exposed_to_internet is not part of the score: score 0 with a reason
    id: "svc:transcendence:nginx",
    label: "nginx",
    status: "healthy",
    score: 0,
    reasons: ["exposed_to_internet"],
  },
];
