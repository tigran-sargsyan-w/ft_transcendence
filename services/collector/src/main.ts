import Fastify from 'fastify';

import { DockerClient } from './docker-client.js';
import { SnapshotService } from './snapshot.js';

import {
  EventStore,
  SequenceExpiredError,
} from './event-store.js';

import { EventWatcher } from './event-watcher.js';

const PORT = Number(process.env.PORT ?? 3001);
const HOST = process.env.HOST ?? '0.0.0.0';

const DOCKER_API_URL =
  process.env.DOCKER_API_URL ?? 'http://docker-proxy:2375';

const ENVIRONMENT_ID =
  process.env.ENVIRONMENT_ID ?? 'env_local_compose';

const server = Fastify({
  logger: true,
});

const docker = new DockerClient(DOCKER_API_URL);

const events = new EventStore(
  ENVIRONMENT_ID,
  1000,
);

const snapshots = new SnapshotService(
  docker,
  events,
  ENVIRONMENT_ID,
);

const watcher = new EventWatcher(
  docker,
  events,
);

server.get('/api/v1/health', async (_request, reply) => {
  const dockerAvailable = await docker.ping();

  if (!dockerAvailable) {
    return reply.status(503).send({
      error: {
        code: 'COLLECTOR_DOCKER_UNAVAILABLE',
        message: 'Docker Engine is unavailable',
      },
    });
  }

  const version = await docker.version();

  return {
    data: {
      status: 'ok',
      docker: {
        status: 'ok',
        version: version.Version,
        apiVersion: version.ApiVersion,
      },
    },
  };
});

server.get('/api/v1/snapshot', async (_request, reply) => {
  try {
    const snapshot = await snapshots.create();

    return {
      data: snapshot,
    };
  } catch (error) {
    server.log.error(error);

    return reply.status(503).send({
      error: {
        code: 'COLLECTOR_DOCKER_UNAVAILABLE',
        message: 'Docker Engine is unavailable',
      },
    });
  }
});

server.get<{
  Querystring: {
    after?: string;
  };
}>('/api/v1/events', async (request, reply) => {
  const rawAfter = request.query.after;

  if (
    rawAfter === undefined ||
    !/^\d+$/.test(rawAfter)
  ) {
    return reply.status(400).send({
      error: {
        code: 'COLLECTOR_INVALID_REQUEST',
        message: 'after must be a non-negative integer',
      },
    });
  }

  const after = Number(rawAfter);

  if (!Number.isSafeInteger(after)) {
    return reply.status(400).send({
      error: {
        code: 'COLLECTOR_INVALID_REQUEST',
        message: 'after must be a non-negative integer',
      },
    });
  }

  try {
    const collected = await events.waitForEvents(
      after,
      25_000,
    );

    return {
      data: {
        streamId: events.streamId,
        events: collected,
      },
    };
  } catch (error) {
    if (error instanceof SequenceExpiredError) {
      return reply.status(409).send({
        error: {
          code: 'COLLECTOR_SEQUENCE_EXPIRED',
          message:
            'Requested event sequence is no longer available',
        },
      });
    }

    throw error;
  }
});

async function start(): Promise<void> {
  try {
    await docker.version();

    void watcher.run();

    await server.listen({
      port: PORT,
      host: HOST,
    });
  } catch (error) {
    server.log.error(error);
    process.exit(1);
  }
}

await start();