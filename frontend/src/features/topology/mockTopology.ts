import type { TopologySnapshot } from './types'

/**
 * Dashboard mock: a Compose project with every node status, plus a network
 * and the internet node to check that only services are shown.
 * Ids follow the live topology shapes (docs/architecture/realtime-topology.md).
 */
export const mockTopology = {
  schemaVersion: 1,
  environmentId: 'env_local_compose',
  capturedAt: '2026-11-09T09:30:00Z',
  nodes: [
    { id: 'svc:transcendence:nginx', kind: 'service', label: 'nginx', status: 'healthy' },
    { id: 'svc:transcendence:frontend', kind: 'service', label: 'frontend', status: 'healthy' },
    { id: 'svc:transcendence:backend', kind: 'service', label: 'backend', status: 'degraded' },
    { id: 'svc:transcendence:postgres', kind: 'service', label: 'postgres', status: 'healthy' },
    { id: 'svc:transcendence:vault', kind: 'service', label: 'vault', status: 'healthy' },
    { id: 'svc:transcendence:collector', kind: 'service', label: 'collector', status: 'down' },
    { id: 'svc:transcendence:graph-engine', kind: 'service', label: 'graph-engine', status: 'healthy' },
    { id: 'svc:transcendence:redis', kind: 'service', label: 'redis', status: 'unknown' },
    { id: 'svc:transcendence:auth', kind: 'service', label: 'auth', status: 'compromised' },
    { id: 'net:transcendence_default', kind: 'network', label: 'transcendence_default', status: 'healthy' },
    { id: 'ext:internet', kind: 'external', label: 'internet', status: 'unknown' },
  ],
  edges: [],
} satisfies TopologySnapshot
