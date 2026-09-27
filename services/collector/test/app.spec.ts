import {
  describe,
  expect,
  it,
  vi,
} from 'vitest';

import { createApp } from '../src/app.js';
import {
  SequenceExpiredError,
} from '../src/event-store.js';

function makeDependencies() {
  return {
    docker: {
      ping: vi.fn(
        async () => true,
      ),

      version: vi.fn(
        async () => ({
          Version: '28.0.0',
          ApiVersion: '1.48',
          MinAPIVersion: '1.24',
        }),
      ),
    },

    snapshots: {
      create: vi.fn(
        async () => ({
          schemaVersion: 1,
          streamId: 'stream-1',
          environmentId: 'env-test',
          capturedAt:
            '2026-09-27T12:00:00.000Z',
          sequence: 0,
          containers: [],
          networks: [],
          volumes: [],
        }),
      ),
    },

    events: {
      waitForEvents: vi.fn(
        async () => ({
          streamId: 'stream-1',
          events: [],
        }),
      ),
    },
  };
}

describe('Collector HTTP API', () => {
  it('returns collector and Docker health', async () => {
    const deps =
      makeDependencies();

    const app = createApp({
      ...deps,
      logger: false,
    });

    const response =
      await app.inject({
        method: 'GET',
        url: '/api/v1/health',
      });

    expect(response.statusCode).toBe(
      200,
    );

    expect(response.json()).toEqual({
      data: {
        status: 'ok',
        docker: {
          status: 'ok',
          version: '28.0.0',
          apiVersion: '1.48',
        },
      },
    });

    await app.close();
  });

  it('returns 503 when Docker ping fails', async () => {
    const deps =
      makeDependencies();

    deps.docker.ping.mockResolvedValue(
      false,
    );

    const app = createApp({
      ...deps,
      logger: false,
    });

    const response =
      await app.inject({
        method: 'GET',
        url: '/api/v1/health',
      });

    expect(response.statusCode).toBe(
      503,
    );

    expect(response.json()).toEqual({
      error: {
        code:
          'COLLECTOR_DOCKER_UNAVAILABLE',
        message:
          'Docker Engine is unavailable',
      },
    });

    await app.close();
  });

  it('returns 503 when Docker version request fails', async () => {
    const deps =
      makeDependencies();

    deps.docker.version.mockRejectedValue(
      new Error(
        'Docker Engine is unavailable',
      ),
    );

    const app = createApp({
      ...deps,
      logger: false,
    });

    const response =
      await app.inject({
        method: 'GET',
        url: '/api/v1/health',
      });

    expect(response.statusCode).toBe(
      503,
    );

    expect(response.json()).toEqual({
      error: {
        code:
          'COLLECTOR_DOCKER_UNAVAILABLE',
        message:
          'Docker Engine is unavailable',
      },
    });

    await app.close();
  });

  it('returns a snapshot', async () => {
    const deps =
      makeDependencies();

    const app = createApp({
      ...deps,
      logger: false,
    });

    const response =
      await app.inject({
        method: 'GET',
        url: '/api/v1/snapshot',
      });

    expect(response.statusCode).toBe(
      200,
    );

    expect(response.json().data).toMatchObject({
      streamId: 'stream-1',
      environmentId: 'env-test',
      sequence: 0,
    });

    await app.close();
  });

  it('returns 503 when snapshot collection fails', async () => {
    const deps =
      makeDependencies();

    deps.snapshots.create.mockRejectedValue(
      new Error(
        'Docker Engine is unavailable',
      ),
    );

    const app = createApp({
      ...deps,
      logger: false,
    });

    const response =
      await app.inject({
        method: 'GET',
        url: '/api/v1/snapshot',
      });

    expect(response.statusCode).toBe(
      503,
    );

    await app.close();
  });

  it.each([
    '/api/v1/events',
    '/api/v1/events?after=abc',
    '/api/v1/events?after=-1',
    '/api/v1/events?after=1.5',
    '/api/v1/events?after=9007199254740992',
  ])(
    'rejects invalid event cursor: %s',
    async (url) => {
      const deps =
        makeDependencies();

      const app = createApp({
        ...deps,
        logger: false,
      });

      const response =
        await app.inject({
          method: 'GET',
          url,
        });

      expect(
        response.statusCode,
      ).toBe(400);

      expect(
        response.json().error.code,
      ).toBe(
        'COLLECTOR_INVALID_REQUEST',
      );

      await app.close();
    },
  );

  it('returns event batches', async () => {
    const deps =
      makeDependencies();

    deps.events.waitForEvents.mockResolvedValue(
      {
        streamId: 'stream-1',
        events: [],
      },
    );

    const app = createApp({
      ...deps,
      logger: false,
    });

    const response =
      await app.inject({
        method: 'GET',
        url:
          '/api/v1/events?after=0',
      });

    expect(response.statusCode).toBe(
      200,
    );

    expect(response.json()).toEqual({
      data: {
        streamId: 'stream-1',
        events: [],
      },
    });

    expect(
      deps.events.waitForEvents,
    ).toHaveBeenCalledWith(
      0,
      25_000,
    );

    await app.close();
  });

  it('returns 409 for an expired event sequence', async () => {
    const deps =
      makeDependencies();

    deps.events.waitForEvents.mockRejectedValue(
      new SequenceExpiredError(),
    );

    const app = createApp({
      ...deps,
      logger: false,
    });

    const response =
      await app.inject({
        method: 'GET',
        url:
          '/api/v1/events?after=0',
      });

    expect(response.statusCode).toBe(
      409,
    );

    expect(
      response.json().error.code,
    ).toBe(
      'COLLECTOR_SEQUENCE_EXPIRED',
    );

    await app.close();
  });
});