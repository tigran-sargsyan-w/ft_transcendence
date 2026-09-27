import {
  describe,
  expect,
  it,
} from 'vitest';

import { EventStore } from '../src/event-store.js';
import { SnapshotService } from '../src/snapshot.js';

import type { DockerClient } from '../src/docker-client.js';

function makeDockerMock(
  overrides: Partial<DockerClient> = {},
): DockerClient {
  return {
    listContainers: async () => [],
    inspectContainer: async () => {
      throw new Error('unexpected inspect');
    },
    listNetworks: async () => [],
    listVolumes: async () => [],
    ...overrides,
  } as unknown as DockerClient;
}

describe('SnapshotService', () => {
  it('creates a snapshot with the current stream checkpoint', async () => {
    const events = new EventStore(
      'env-test',
    );

    events.append({
      occurredAt:
        '2026-09-27T12:00:00.000Z',
      type: 'container.started',
      resource: {
        kind: 'container',
        id: 'container-1',
      },
      data: {},
    });

    const docker = makeDockerMock();

    const service = new SnapshotService(
      docker,
      events,
      'env-test',
    );

    const snapshot =
      await service.create();

    expect(snapshot).toMatchObject({
      schemaVersion: 1,
      streamId: events.streamId,
      environmentId: 'env-test',
      sequence: 1,
      containers: [],
      networks: [],
      volumes: [],
    });

    expect(
      Date.parse(snapshot.capturedAt),
    ).not.toBeNaN();
  });

  it('keeps streamId and sequence from the same checkpoint', async () => {
    const events = new EventStore(
      'env-test',
    );

    events.append({
      occurredAt:
        '2026-09-27T12:00:00.000Z',
      type: 'container.started',
      resource: {
        kind: 'container',
        id: 'container-1',
      },
      data: {},
    });

    const originalCheckpoint =
      events.checkpoint();

    const docker = makeDockerMock({
      listContainers: async () => {
        // Simulate Docker event-stream reset
        // while the snapshot is being collected.
        events.resetStream();

        return [];
      },
    });

    const service = new SnapshotService(
      docker,
      events,
      'env-test',
    );

    const snapshot =
      await service.create();

    expect(snapshot.streamId).toBe(
      originalCheckpoint.streamId,
    );

    expect(snapshot.sequence).toBe(
      originalCheckpoint.sequence,
    );
  });

  it('collects and normalizes Docker resources into the snapshot', async () => {
    const events = new EventStore('env-test');

    const docker = makeDockerMock({
      listContainers: async () => [
        {
          Id: 'container-1',
        },
      ],

      inspectContainer: async () => ({
        Id: 'container-1',
        Name: '/api',
        Config: {
          Image: 'example/api:latest',
          Labels: null,
        },
        State: {
          Status: 'running',
        },
        NetworkSettings: {
          Ports: null,
          Networks: null,
        },
        Mounts: [],
      }),

      listNetworks: async () => [
        {
          Id: 'network-1',
          Name: 'app-network',
          Driver: 'bridge',
          Internal: false,
          Labels: null,
        },
      ],

      listVolumes: async () => [
        {
          Name: 'postgres-data',
          Driver: 'local',
          Labels: null,
        },
      ],
    });

    const service = new SnapshotService(
      docker,
      events,
      'env-test',
    );

    const snapshot = await service.create();

    expect(snapshot.containers).toEqual([
      {
        id: 'container-1',
        name: 'api',
        image: 'example/api:latest',
        state: 'running',
        health: 'none',
        labels: {},
        compose: null,
        ports: [],
        networks: [],
        mounts: [],
      },
    ]);

    expect(snapshot.networks).toEqual([
      {
        id: 'network-1',
        name: 'app-network',
        driver: 'bridge',
        internal: false,
        labels: {},
      },
    ]);

    expect(snapshot.volumes).toEqual([
      {
        name: 'postgres-data',
        driver: 'local',
        labels: {},
      },
    ]);
  });
});