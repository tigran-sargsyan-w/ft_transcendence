// Shapes of the Collector HTTP API (docs/architecture/collector-contract.md).
// Copied, not imported: the collector is a separate package.

export type ContainerState =
  | 'created'
  | 'running'
  | 'paused'
  | 'restarting'
  | 'removing'
  | 'exited'
  | 'dead';

export type ContainerHealth = 'healthy' | 'unhealthy' | 'starting' | 'none';

export type CollectorContainer = {
  id: string;
  name: string;
  image: string;
  state: ContainerState;
  health: ContainerHealth;
  labels: Record<string, string>;
  compose: { project: string; service: string; dependsOn: string[] } | null;
  ports: {
    containerPort: number;
    protocol: string;
    hostIp: string;
    hostPort: number;
  }[];
  networks: { networkId: string; name: string; ipv4Address: string }[];
  mounts: {
    type: string;
    source: string;
    destination: string;
    readOnly: boolean;
  }[];
};

export type CollectorNetwork = {
  id: string;
  name: string;
  driver: string;
  internal: boolean;
  labels: Record<string, string>;
};

export type CollectorVolume = {
  name: string;
  driver: string;
  labels: Record<string, string>;
};

export type CollectorSnapshot = {
  schemaVersion: number;
  streamId: string;
  environmentId: string;
  capturedAt: string;
  sequence: number;
  containers: CollectorContainer[];
  networks: CollectorNetwork[];
  volumes: CollectorVolume[];
};

export type CollectorEventType =
  | 'container.created'
  | 'container.started'
  | 'container.stopped'
  | 'container.died'
  | 'container.destroyed'
  | 'container.health_changed'
  | 'network.created'
  | 'network.removed'
  | 'network.connected'
  | 'network.disconnected'
  | 'volume.created'
  | 'volume.removed';

export type CollectorEvent = {
  schemaVersion: number;
  streamId: string;
  environmentId: string;
  eventId: string;
  sequence: number;
  occurredAt: string;
  type: CollectorEventType;
  resource: { kind: 'container' | 'network' | 'volume'; id: string };
  data: Record<string, unknown>;
};

// Answer of GET /events: every event shares the batch's streamId.
export type EventBatch = {
  streamId: string;
  events: CollectorEvent[];
};
