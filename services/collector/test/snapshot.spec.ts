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
});