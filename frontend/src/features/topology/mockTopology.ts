import type { EdgeKind, GraphEdge, TopologySnapshot } from './types'

/**
 * Dashboard mock: a Compose project with every node status, plus the host,
 * a network, a volume and the internet node to check that only services are
 * shown. Ids and edges follow docs/architecture/topology-mapping.md; the
 * depends_on edges mirror compose.yml.
 */

const HOST = 'host:env_local_compose'
const NETWORK = 'net:transcendence_default'

const SERVICES = [
  'nginx',
  'frontend',
  'backend',
  'postgres',
  'vault',
  'collector',
  'graph-engine',
  'redis',
  'auth',
].map((name) => `svc:transcendence:${name}`)

// Edge ids are built like the backend: `<kind>:<source>><target>`.
const edge = (
  kind: EdgeKind,
  source: string,
  target: string,
  metadata?: Record<string, unknown>,
): GraphEdge => ({
  id: `${kind}:${source}>${target}`,
  source,
  target,
  kind,
  ...(metadata && { metadata }),
})

export const mockTopology = {
  schemaVersion: 1,
  environmentId: 'env_local_compose',
  capturedAt: '2026-11-09T09:30:00Z',
  nodes: [
    { id: 'svc:transcendence:nginx', kind: 'service', label: 'nginx', status: 'healthy' },
    { id: 'svc:transcendence:frontend', kind: 'service', label: 'frontend', status: 'healthy' },
    { id: 'svc:transcendence:backend', kind: 'service', label: 'backend', status: 'degraded' },
    { id: 'svc:transcendence:postgres', kind: 'service', label: 'postgres', status: 'down' },
    { id: 'svc:transcendence:vault', kind: 'service', label: 'vault', status: 'healthy' },
    { id: 'svc:transcendence:collector', kind: 'service', label: 'collector', status: 'down' },
    { id: 'svc:transcendence:graph-engine', kind: 'service', label: 'graph-engine', status: 'healthy' },
    { id: 'svc:transcendence:redis', kind: 'service', label: 'redis', status: 'unknown' },
    { id: 'svc:transcendence:auth', kind: 'service', label: 'auth', status: 'compromised' },
    { id: HOST, kind: 'host', label: 'Docker host', status: 'healthy' },
    { id: NETWORK, kind: 'network', label: 'transcendence_default', status: 'healthy' },
    { id: 'vol:postgres_data', kind: 'volume', label: 'postgres_data', status: 'healthy' },
    { id: 'ext:internet', kind: 'external', label: 'Internet', status: 'unknown' },
  ],
  edges: [
    // Compose depends_on (same as compose.yml)
    edge('depends_on', 'svc:transcendence:nginx', 'svc:transcendence:frontend'),
    edge('depends_on', 'svc:transcendence:nginx', 'svc:transcendence:backend'),
    edge('depends_on', 'svc:transcendence:frontend', 'svc:transcendence:backend'),
    edge('depends_on', 'svc:transcendence:backend', 'svc:transcendence:postgres'),
    edge('depends_on', 'svc:transcendence:backend', 'svc:transcendence:vault'),

    // Named volume
    edge('mounts', 'svc:transcendence:postgres', 'vol:postgres_data', { readOnly: false }),

    // Published port reachable from outside
    edge('exposes', 'ext:internet', 'svc:transcendence:nginx', {
      ports: [{ containerPort: 443, protocol: 'tcp', hostPort: 8443 }],
    }),

    // Every service runs on the host and is attached to the network
    ...SERVICES.map((svc) => edge('runs_on', svc, HOST)),
    ...SERVICES.map((svc) => edge('connects_to', svc, NETWORK)),
  ],
} satisfies TopologySnapshot
