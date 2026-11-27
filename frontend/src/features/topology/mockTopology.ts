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

type PublishedPort = {
  containerPort: number
  protocol: string
  hostPort: number
  loopbackOnly: boolean
}

// Service metadata as the backend builds it (build-graph.ts, topology-mapping.md):
// image, composeProject, composeService and the published ports.
const serviceMetadata = (
  name: string,
  image: string,
  ports: PublishedPort[] = [],
) => ({
  image,
  composeProject: 'transcendence',
  composeService: name,
  ports,
})

export const mockTopology = {
  schemaVersion: 1,
  environmentId: 'env_local_compose',
  capturedAt: '2026-11-09T09:30:00Z',
  nodes: [
    {
      id: 'svc:transcendence:nginx',
      kind: 'service',
      label: 'nginx',
      status: 'healthy',
      metadata: serviceMetadata('nginx', 'nginx:1.28-alpine', [
        { containerPort: 80, protocol: 'tcp', hostPort: 8080, loopbackOnly: true },
        { containerPort: 443, protocol: 'tcp', hostPort: 8443, loopbackOnly: false },
      ]),
    },
    {
      id: 'svc:transcendence:frontend',
      kind: 'service',
      label: 'frontend',
      status: 'healthy',
      metadata: serviceMetadata('frontend', 'transcendence-frontend'),
    },
    {
      id: 'svc:transcendence:backend',
      kind: 'service',
      label: 'backend',
      status: 'degraded',
      metadata: serviceMetadata('backend', 'transcendence-backend'),
    },
    {
      id: 'svc:transcendence:postgres',
      kind: 'service',
      label: 'postgres',
      status: 'down',
      metadata: serviceMetadata('postgres', 'transcendence-postgres'),
    },
    {
      id: 'svc:transcendence:vault',
      kind: 'service',
      label: 'vault',
      status: 'healthy',
      metadata: serviceMetadata('vault', 'hashicorp/vault:2.2.1'),
    },
    {
      id: 'svc:transcendence:collector',
      kind: 'service',
      label: 'collector',
      status: 'down',
      metadata: serviceMetadata('collector', 'transcendence-collector'),
    },
    {
      id: 'svc:transcendence:graph-engine',
      kind: 'service',
      label: 'graph-engine',
      status: 'healthy',
      metadata: serviceMetadata('graph-engine', 'transcendence-graph-engine'),
    },
    {
      id: 'svc:transcendence:redis',
      kind: 'service',
      label: 'redis',
      status: 'unknown',
      metadata: serviceMetadata('redis', 'redis:7.4-alpine'),
    },
    {
      id: 'svc:transcendence:auth',
      kind: 'service',
      label: 'auth',
      status: 'compromised',
      metadata: serviceMetadata('auth', 'transcendence-auth'),
    },
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
