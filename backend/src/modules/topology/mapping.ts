import type { CollectorContainer } from './collector.types.js';
import type { EdgeKind, NodeStatus } from './graph.types.js';

// Ids are opaque to clients. `:` and `>` are separators: a Compose project or
// service name cannot contain `:`, so `a_b` + `c` and `a` + `b_c` stay apart.
export const serviceId = (project: string, service: string) =>
  `svc:${project}:${service}`;

// A container outside Compose is its own service, identified by its name.
export const standaloneId = (name: string) => `ctr:${name}`;

export const networkNodeId = (name: string) => `net:${name}`;

export const volumeNodeId = (name: string) => `vol:${name}`;

export const hostNodeId = (environmentId: string) => `host:${environmentId}`;

export const EXTERNAL_NODE_ID = 'ext:internet';

export const edgeId = (kind: EdgeKind, source: string, target: string) =>
  `${kind}:${source}>${target}`;

// Docker state + health -> node status. `compromised` is never produced here.
//
//   running, healthy or no healthcheck   healthy
//   running, unhealthy                   degraded
//   restarting                           degraded
//   running, healthcheck starting        unknown
//   created                              unknown
//   exited, dead, paused, removing       down
//   any state this version does not know unknown
export function containerStatus(
  container: Pick<CollectorContainer, 'state' | 'health'>,
): NodeStatus {
  switch (container.state) {
    case 'running':
      if (container.health === 'unhealthy') return 'degraded';
      if (container.health === 'starting') return 'unknown';
      return 'healthy';
    case 'restarting':
      return 'degraded';
    case 'exited':
    case 'dead':
    case 'paused':
    case 'removing':
      return 'down';
    default:
      return 'unknown';
  }
}

const STATUS_RANK: Record<NodeStatus, number> = {
  healthy: 0,
  degraded: 1,
  unknown: 2,
  down: 3,
  compromised: 4,
};

export const statusRank = (status: NodeStatus) => STATUS_RANK[status];

// A service with several instances (Compose recreation, scaling) shows its
// best one. Limit: a crashed replica is hidden while another one runs.
export function bestStatus(statuses: NodeStatus[]): NodeStatus {
  return statuses.reduce((best, status) =>
    statusRank(status) < statusRank(best) ? status : best,
  );
}
