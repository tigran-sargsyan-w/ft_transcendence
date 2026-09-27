import {
  describe,
  expect,
  it,
} from 'vitest';

import { EventStore } from '../src/event-store.js';
import { EventWatcher } from '../src/event-watcher.js';

import type {
  DockerClient,
  DockerContainerInspect,
  DockerEvent,
} from '../src/docker-client.js';

type TestableEventWatcher = {
  consumeStream(): Promise<void>;
};

function makeContainer(
  id = 'container-1',
): DockerContainerInspect {
  return {
    Id: id,
    Name: '/test-container',
    Config: {
      Image: 'hello-world',
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
  };
}

function streamEvents(
  events: DockerEvent[],
): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();

  return new ReadableStream<Uint8Array>({
    start(controller) {
      for (const event of events) {
        controller.enqueue(
          encoder.encode(
            `${JSON.stringify(event)}\n`,
          ),
        );
      }

      controller.close();
    },
  });
}

function dockerEvent(
  overrides: Partial<DockerEvent>,
): DockerEvent {
  return {
    Type: 'container',
    Action: 'start',
    Actor: {
      ID: 'container-1',
      Attributes: {},
    },
    time: 1_700_000_000,
    timeNano: 0,
    ...overrides,
  };
}

function makeDockerMock(
  overrides: Partial<DockerClient>,
): DockerClient {
  return {
    inspectContainer: async (id: string) =>
      makeContainer(id),
    inspectNetwork: async () => ({
      Id: 'network-1',
      Name: 'test-network',
      Driver: 'bridge',
      Internal: false,
      Labels: null,
    }),
    inspectVolume: async () => ({
      Name: 'test-volume',
      Driver: 'local',
      Labels: null,
    }),
    ...overrides,
  } as unknown as DockerClient;
}

async function consume(
  watcher: EventWatcher,
): Promise<void> {
  await (
    watcher as unknown as TestableEventWatcher
  ).consumeStream();
}

describe('EventWatcher', () => {
  it('maps container lifecycle events in stream order', async () => {
    const store = new EventStore('env-test');

    const docker = makeDockerMock({
      eventStream: async () =>
        streamEvents([
          dockerEvent({
            Action: 'create',
          }),
          dockerEvent({
            Action: 'start',
          }),
          dockerEvent({
            Action: 'stop',
          }),
          dockerEvent({
            Action: 'destroy',
          }),
        ]),
    });

    const watcher = new EventWatcher(
      docker,
      store,
    );

    await consume(watcher);

    const result =
      await store.waitForEvents(0, 10);

    expect(
      result.events.map((event) => event.type),
    ).toEqual([
      'container.created',
      'container.started',
      'container.stopped',
      'container.destroyed',
    ]);

    expect(
      result.events.map(
        (event) => event.sequence,
      ),
    ).toEqual([1, 2, 3, 4]);
  });

  it('preserves container.died when inspect is no longer possible', async () => {
    const store = new EventStore('env-test');

    const docker = makeDockerMock({
      inspectContainer: async () => {
        throw new Error(
          'container no longer exists',
        );
      },

      eventStream: async () =>
        streamEvents([
          dockerEvent({
            Action: 'die',
            Actor: {
              ID: 'container-1',
              Attributes: {
                exitCode: '0',
              },
            },
          }),
        ]),
    });

    const watcher = new EventWatcher(
      docker,
      store,
    );

    await consume(watcher);

    const result =
      await store.waitForEvents(0, 10);

    expect(result.events).toHaveLength(1);

    expect(result.events[0]).toMatchObject({
      type: 'container.died',
      resource: {
        kind: 'container',
        id: 'container-1',
      },
      data: {
        exitCode: 0,
      },
    });
  });

  it('maps network connect and disconnect events', async () => {
    const store = new EventStore('env-test');

    const docker = makeDockerMock({
      eventStream: async () =>
        streamEvents([
          dockerEvent({
            Type: 'network',
            Action: 'connect',
            Actor: {
              ID: 'network-1',
              Attributes: {
                container: 'container-1',
              },
            },
          }),
          dockerEvent({
            Type: 'network',
            Action: 'disconnect',
            Actor: {
              ID: 'network-1',
              Attributes: {
                container: 'container-1',
              },
            },
          }),
        ]),
    });

    const watcher = new EventWatcher(
      docker,
      store,
    );

    await consume(watcher);

    const result =
      await store.waitForEvents(0, 10);

    expect(
      result.events.map((event) => ({
        type: event.type,
        data: event.data,
      })),
    ).toEqual([
      {
        type: 'network.connected',
        data: {
          containerId: 'container-1',
        },
      },
      {
        type: 'network.disconnected',
        data: {
          containerId: 'container-1',
        },
      },
    ]);
  });

  it('maps volume create and remove events', async () => {
    const store = new EventStore('env-test');

    const docker = makeDockerMock({
      eventStream: async () =>
        streamEvents([
          dockerEvent({
            Type: 'volume',
            Action: 'create',
            Actor: {
              ID: 'test-volume',
              Attributes: {},
            },
          }),
          dockerEvent({
            Type: 'volume',
            Action: 'destroy',
            Actor: {
              ID: 'test-volume',
              Attributes: {},
            },
          }),
        ]),
    });

    const watcher = new EventWatcher(
      docker,
      store,
    );

    await consume(watcher);

    const result =
      await store.waitForEvents(0, 10);

    expect(
      result.events.map((event) => event.type),
    ).toEqual([
      'volume.created',
      'volume.removed',
    ]);

    expect(
      result.events[0]?.data,
    ).toEqual({
      volume: {
        name: 'test-volume',
        driver: 'local',
        labels: {},
      },
    });
  });

  it('ignores unsupported Docker events', async () => {
    const store = new EventStore('env-test');

    const docker = makeDockerMock({
      eventStream: async () =>
        streamEvents([
          dockerEvent({
            Action: 'rename',
          }),
        ]),
    });

    const watcher = new EventWatcher(
      docker,
      store,
    );

    await consume(watcher);

    const result =
      await store.waitForEvents(0, 5);

    expect(result.events).toEqual([]);
  });
});