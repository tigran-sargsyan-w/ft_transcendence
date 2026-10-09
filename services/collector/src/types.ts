export type DockerLabels = Record<string, string>;

export type ContainerState =
  | 'created'
  | 'running'
  | 'paused'
  | 'restarting'
  | 'removing'
  | 'exited'
  | 'dead';

export type ContainerHealth =
  | 'healthy'
  | 'unhealthy'
  | 'starting'
  | 'none';

export type CollectorContainer = {
  id: string;
  name: string;
  image: string;
  state: ContainerState;
  health: ContainerHealth;
  labels: DockerLabels;
  compose: {
    project: string;
    service: string;
    dependsOn: string[];
  } | null;
  ports: Array<{
    containerPort: number;
    protocol: string;
    hostIp: string;
    hostPort: number;
  }>;
  networks: Array<{
    networkId: string;
    name: string;
    ipv4Address: string;
  }>;
  mounts: Array<{
    type: string;
    source: string;
    destination: string;
    readOnly: boolean;
  }>;
};

export type CollectorNetwork = {
  id: string;
  name: string;
  driver: string;
  internal: boolean;
  labels: DockerLabels;
};

export type CollectorVolume = {
  name: string;
  driver: string;
  labels: DockerLabels;
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

export type CollectorResourceKind =
  | 'container'
  | 'network'
  | 'volume';

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
  resource: {
    kind: CollectorResourceKind;
    id: string;
  };
  data: Record<string, unknown>;
};