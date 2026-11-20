/**
 * HYPOTHETICAL: no backend contract sends these fields yet.
 *
 * - role: not in the graph contract nor in the node metadata.
 * - lastEvent: the collector events stay inside Nest; no route exposes them
 *   per service (topology_history is stored but not served, ADR 0004).
 *
 * The event types are the collector's (docs/architecture/collector-contract.md)
 * and each one matches the status of the node in mockTopology. Keyed by the
 * node id of mockTopology. Replace this file once the backend agrees on a shape.
 */

export type ServiceEventType =
  | "container.created"
  | "container.started"
  | "container.stopped"
  | "container.died"
  | "container.health_changed"
  | "container.destroyed";

export type ServiceDetails = {
  role: string;
  lastEvent: {
    type: ServiceEventType;
    at: string; // ISO 8601 UTC, at or before mockTopology.capturedAt
  };
};

export const mockServiceDetails: Record<string, ServiceDetails> = {
  "svc:transcendence:nginx": {
    role: "Reverse proxy",
    lastEvent: { type: "container.started", at: "2026-11-09T08:12:04Z" },
  },
  "svc:transcendence:frontend": {
    role: "Web app",
    lastEvent: { type: "container.started", at: "2026-11-09T08:12:01Z" },
  },
  "svc:transcendence:backend": {
    // degraded: running but its healthcheck turned unhealthy
    role: "API",
    lastEvent: { type: "container.health_changed", at: "2026-11-09T09:27:41Z" },
  },
  "svc:transcendence:postgres": {
    // down: the container crashed
    role: "Database",
    lastEvent: { type: "container.died", at: "2026-11-09T09:24:13Z" },
  },
  "svc:transcendence:vault": {
    role: "Secrets",
    lastEvent: { type: "container.started", at: "2026-11-09T08:11:52Z" },
  },
  "svc:transcendence:collector": {
    // down: stopped by hand
    role: "Monitoring",
    lastEvent: { type: "container.stopped", at: "2026-11-09T09:05:30Z" },
  },
  "svc:transcendence:graph-engine": {
    role: "Analysis",
    lastEvent: { type: "container.started", at: "2026-11-09T08:12:00Z" },
  },
  "svc:transcendence:redis": {
    // unknown: created, not started yet
    role: "Cache",
    lastEvent: { type: "container.created", at: "2026-11-09T09:29:58Z" },
  },
  "svc:transcendence:auth": {
    // compromised never comes from the collector: Docker sees it running
    role: "Authentication",
    lastEvent: { type: "container.started", at: "2026-11-09T08:12:03Z" },
  },
};
