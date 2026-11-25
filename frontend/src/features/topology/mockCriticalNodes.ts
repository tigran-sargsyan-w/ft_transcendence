import type { CriticalNodeRow } from "./types";

// Sorted like the engine: score descending, then id.
// Reasons follow the engine order: high_betweenness, many_dependents,
// exposed_to_internet (structural_role and single_node are always alone).
export const mockCriticalNodes: CriticalNodeRow[] = [
  {
    // 3 reasons: the maximum the engine can send
    id: "svc:demo:api-gateway",
    label: "api-gateway",
    status: "healthy",
    score: 0.91,
    reasons: ["high_betweenness", "many_dependents", "exposed_to_internet"],
  },
  {
    id: "svc:demo:db",
    label: "postgres",
    status: "healthy",
    score: 0.82,
    reasons: ["high_betweenness", "many_dependents"],
  },
  {
    id: "svc:demo:api",
    label: "api",
    status: "degraded",
    score: 0.64,
    reasons: ["many_dependents", "exposed_to_internet"],
  },
  {
    id: "svc:demo:cache",
    label: "redis",
    status: "healthy",
    score: 0.4,
    reasons: ["many_dependents"],
  },
  {
    // Unknown code: the engine may add new ones, the UI needs a fallback label
    id: "svc:demo:queue",
    label: "rabbitmq",
    status: "unknown",
    score: 0.27,
    reasons: ["high_betweenness", "single_point_of_failure"],
  },
  {
    id: "svc:demo:worker",
    label: "worker",
    status: "down",
    score: 0.12,
    reasons: ["structural_role"],
  },
  {
    // exposed_to_internet is not part of the score: score 0 with a reason
    id: "svc:demo:nginx",
    label: "nginx",
    status: "healthy",
    score: 0,
    reasons: ["exposed_to_internet"],
  },
];
