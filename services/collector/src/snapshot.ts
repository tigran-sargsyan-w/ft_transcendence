import { EventStore } from './event-store.js';
import { DockerClient } from './docker-client.js';

import {
  normalizeContainer,
  normalizeNetwork,
  normalizeVolume,
} from './normalize.js';

import type { CollectorSnapshot } from './types.js';

export class SnapshotService {
  private readonly environmentId: string;

  constructor(
    private readonly docker: DockerClient,
    private readonly events: EventStore,
    environmentId: string,
  ) {
    this.environmentId = environmentId;
  }

  async create(): Promise<CollectorSnapshot> {
    const checkpoint = this.events.checkpoint();
    const containerSummaries = await this.docker.listContainers();

    const inspectedContainers = await Promise.all(
      containerSummaries.map((container) =>
        this.docker.inspectContainer(container.Id),
      ),
    );

    const [networks, volumes] = await Promise.all([
      this.docker.listNetworks(),
      this.docker.listVolumes(),
    ]);

    return {
      schemaVersion: 1,
      streamId: checkpoint.streamId,
      environmentId: this.environmentId,
      capturedAt: new Date().toISOString(),
      sequence: checkpoint.sequence,
      containers: inspectedContainers.map(normalizeContainer),
      networks: networks.map(normalizeNetwork),
      volumes: volumes.map(normalizeVolume),
    };
  }
}