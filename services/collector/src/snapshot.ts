import { randomUUID } from 'node:crypto';

import { DockerClient } from './docker-client.js';

import {
  normalizeContainer,
  normalizeNetwork,
  normalizeVolume,
} from './normalize.js';

import type { CollectorSnapshot } from './types.js';

export class SnapshotService {
  private readonly streamId = randomUUID();
  private readonly environmentId: string;

  constructor(
    private readonly docker: DockerClient,
    environmentId: string,
  ) {
    this.environmentId = environmentId;
  }

  async create(): Promise<CollectorSnapshot> {
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
      streamId: this.streamId,
      environmentId: this.environmentId,
      capturedAt: new Date().toISOString(),
      sequence: 0,
      containers: inspectedContainers.map(normalizeContainer),
      networks: networks.map(normalizeNetwork),
      volumes: volumes.map(normalizeVolume),
    };
  }
}