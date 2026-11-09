// The `demo` Compose project of docs/architecture/collector-contract.md:
// db (healthcheck, volume), api (depends on db, crashes, is recreated) and
// web (depends on api, publishes port 8080). Values come from a real run;
// ids are shortened to 12 characters.
import type {
  CollectorContainer,
  CollectorEvent,
  CollectorEventType,
  CollectorSnapshot,
} from '../../src/modules/topology/collector.types.js';

export const ENVIRONMENT_ID = 'env_local_compose';
export const STREAM_ID = 'stream_demo_01';

const NETWORK_ID = 'e5b899c59c6a';
const NETWORK_NAME = 'demo_default';

export const DB_ID = '6c28d5dff0b8';
export const API_ID = '4589c1f3ac3c';
export const WEB_ID = 'b183ed94a57c';
export const API_RECREATED_ID = 'ea2201557210';

export function container(
  init: Partial<CollectorContainer> & Pick<CollectorContainer, 'id' | 'name'>,
): CollectorContainer {
  return {
    image: 'busybox:1.37',
    state: 'running',
    health: 'none',
    labels: {},
    compose: null,
    ports: [],
    networks: [],
    mounts: [],
    ...init,
  };
}

// A container that is not attached to the network (just created) has an
// empty networkId and no address.
function onNetwork(ipv4Address: string, attached = true) {
  return [
    {
      networkId: attached ? NETWORK_ID : '',
      name: NETWORK_NAME,
      ipv4Address,
    },
  ];
}

const dbVolume = [
  {
    type: 'volume',
    source: 'demo_db_data',
    destination: '/data',
    readOnly: false,
  },
];

const webPorts = [
  { containerPort: 80, protocol: 'tcp', hostIp: '0.0.0.0', hostPort: 8080 },
];

export const db = (init: Partial<CollectorContainer> = {}) =>
  container({
    id: DB_ID,
    name: 'demo-db-1',
    compose: { project: 'demo', service: 'db', dependsOn: [] },
    networks: onNetwork('172.18.0.2'),
    mounts: dbVolume,
    health: 'healthy',
    ...init,
  });

export const api = (init: Partial<CollectorContainer> = {}) =>
  container({
    id: API_ID,
    name: 'demo-api-1',
    compose: { project: 'demo', service: 'api', dependsOn: ['db'] },
    networks: onNetwork('172.18.0.3'),
    ...init,
  });

export const web = (init: Partial<CollectorContainer> = {}) =>
  container({
    id: WEB_ID,
    name: 'demo-web-1',
    compose: { project: 'demo', service: 'web', dependsOn: ['api'] },
    ports: webPorts,
    networks: onNetwork('172.18.0.4'),
    ...init,
  });

export function event(
  sequence: number,
  occurredAt: string,
  type: CollectorEventType,
  resource: CollectorEvent['resource'],
  data: Record<string, unknown>,
): CollectorEvent {
  return {
    schemaVersion: 1,
    streamId: STREAM_ID,
    environmentId: ENVIRONMENT_ID,
    eventId: `evt_${String(sequence).padStart(8, '0')}`,
    sequence,
    occurredAt,
    type,
    resource,
    data,
  };
}

const networkResource = { kind: 'network', id: NETWORK_ID } as const;

const demoNetwork = {
  id: NETWORK_ID,
  name: NETWORK_NAME,
  driver: 'bridge',
  internal: false,
  labels: {},
};

const demoVolume = { name: 'demo_db_data', driver: 'local', labels: {} };

export function snapshot(
  sequence: number,
  containers: CollectorContainer[],
  capturedAt = '2026-09-23T15:38:47.960Z',
): CollectorSnapshot {
  return {
    schemaVersion: 1,
    streamId: STREAM_ID,
    environmentId: ENVIRONMENT_ID,
    capturedAt,
    sequence,
    containers,
    networks: containers.length > 0 ? [demoNetwork] : [],
    volumes: containers.length > 0 ? [demoVolume] : [],
  };
}

// Nothing exists yet, before event 1.
export const emptySnapshot = snapshot(0, [], '2026-09-23T15:38:40.000Z');

// GET /snapshot right after event 12 (`docker compose up`).
export const snapshotAfterUp = snapshot(12, [db(), api(), web()]);

// Events 1 to 12: the `docker compose up`.
export const eventsUp: CollectorEvent[] = [
  event(1, '2026-09-23T15:38:44.353Z', 'network.created', networkResource, {
    network: demoNetwork,
  }),
  event(
    2,
    '2026-09-23T15:38:44.357Z',
    'volume.created',
    { kind: 'volume', id: 'demo_db_data' },
    { volume: demoVolume },
  ),
  event(
    3,
    '2026-09-23T15:38:44.442Z',
    'container.created',
    { kind: 'container', id: DB_ID },
    {
      container: db({
        state: 'created',
        health: 'none',
        networks: onNetwork('', false),
      }),
    },
  ),
  event(
    4,
    '2026-09-23T15:38:44.542Z',
    'container.created',
    { kind: 'container', id: API_ID },
    { container: api({ state: 'created', networks: onNetwork('', false) }) },
  ),
  event(
    5,
    '2026-09-23T15:38:44.620Z',
    'container.created',
    { kind: 'container', id: WEB_ID },
    { container: web({ state: 'created', networks: onNetwork('', false) }) },
  ),
  event(6, '2026-09-23T15:38:44.902Z', 'network.connected', networkResource, {
    containerId: DB_ID,
  }),
  event(
    7,
    '2026-09-23T15:38:44.928Z',
    'container.started',
    { kind: 'container', id: DB_ID },
    { container: db({ health: 'starting' }) },
  ),
  event(
    8,
    '2026-09-23T15:38:46.976Z',
    'container.health_changed',
    { kind: 'container', id: DB_ID },
    { container: db() },
  ),
  event(9, '2026-09-23T15:38:47.610Z', 'network.connected', networkResource, {
    containerId: API_ID,
  }),
  event(
    10,
    '2026-09-23T15:38:47.628Z',
    'container.started',
    { kind: 'container', id: API_ID },
    { container: api() },
  ),
  event(11, '2026-09-23T15:38:47.902Z', 'network.connected', networkResource, {
    containerId: WEB_ID,
  }),
  event(
    12,
    '2026-09-23T15:38:47.923Z',
    'container.started',
    { kind: 'container', id: WEB_ID },
    { container: web() },
  ),
];

// The recreated api: same service, new container id, temporary name first.
export const apiRecreated = (init: Partial<CollectorContainer> = {}) =>
  api({ id: API_RECREATED_ID, ...init });

// Events 13 to 18: api crashes (exit code 1), then
// `docker compose up -d --force-recreate api`.
export const eventsCrashAndRecreate: CollectorEvent[] = [
  event(
    13,
    '2026-09-23T15:38:57.830Z',
    'network.disconnected',
    networkResource,
    { containerId: API_ID },
  ),
  event(
    14,
    '2026-09-23T15:38:57.839Z',
    'container.died',
    { kind: 'container', id: API_ID },
    {
      exitCode: 1,
      container: api({ state: 'exited', networks: onNetwork('') }),
    },
  ),
  event(
    15,
    '2026-09-23T15:39:07.184Z',
    'container.created',
    { kind: 'container', id: API_RECREATED_ID },
    {
      container: apiRecreated({
        name: '4589c1f3ac3c_demo-api-1',
        state: 'created',
        networks: onNetwork('', false),
      }),
    },
  ),
  event(
    16,
    '2026-09-23T15:39:07.209Z',
    'container.destroyed',
    { kind: 'container', id: API_ID },
    {},
  ),
  event(17, '2026-09-23T15:39:07.954Z', 'network.connected', networkResource, {
    containerId: API_RECREATED_ID,
  }),
  event(
    18,
    '2026-09-23T15:39:07.976Z',
    'container.started',
    { kind: 'container', id: API_RECREATED_ID },
    { container: apiRecreated() },
  ),
];

// GET /snapshot right after event 18.
export const snapshotAfterRecreate = snapshot(
  18,
  [db(), apiRecreated(), web()],
  '2026-09-23T15:39:08.100Z',
);
