import Fastify, {
  type FastifyInstance,
} from 'fastify';

import type { DockerClient } from './docker-client.js';
import {
  SequenceExpiredError,
  type EventStore,
} from './event-store.js';
import type { SnapshotService } from './snapshot.js';

type CollectorAppDependencies = {
  docker: Pick<
    DockerClient,
    'ping' | 'version'
  >;

  events: Pick<
    EventStore,
    'waitForEvents'
  >;

  snapshots: Pick<
    SnapshotService,
    'create'
  >;

  logger?: boolean;
};

export function createApp({
  docker,
  events,
  snapshots,
  logger = true,
}: CollectorAppDependencies): FastifyInstance {
  const server = Fastify({
    logger,
  });

  server.get(
    '/api/v1/health',
    async (_request, reply) => {
        try {
        const dockerAvailable =
            await docker.ping();

        if (!dockerAvailable) {
            return reply.status(503).send({
            error: {
                code:
                'COLLECTOR_DOCKER_UNAVAILABLE',
                message:
                'Docker Engine is unavailable',
            },
            });
        }

        const version =
            await docker.version();

        return {
            data: {
            status: 'ok',
            docker: {
                status: 'ok',
                version: version.Version,
                apiVersion:
                version.ApiVersion,
            },
            },
        };
        } catch {
        return reply.status(503).send({
            error: {
            code:
                'COLLECTOR_DOCKER_UNAVAILABLE',
            message:
                'Docker Engine is unavailable',
            },
        });
        }
    },
  );

  server.get(
    '/api/v1/snapshot',
    async (_request, reply) => {
      try {
        const snapshot =
          await snapshots.create();

        return {
          data: snapshot,
        };
      } catch (error) {
        server.log.error(error);

        return reply.status(503).send({
          error: {
            code:
              'COLLECTOR_DOCKER_UNAVAILABLE',
            message:
              'Docker Engine is unavailable',
          },
        });
      }
    },
  );

  server.get<{
    Querystring: {
      after?: string;
    };
  }>(
    '/api/v1/events',
    async (request, reply) => {
      const rawAfter =
        request.query.after;

      if (
        rawAfter === undefined ||
        !/^\d+$/.test(rawAfter)
      ) {
        return reply.status(400).send({
          error: {
            code:
              'COLLECTOR_INVALID_REQUEST',
            message:
              'after must be a non-negative integer',
          },
        });
      }

      const after = Number(rawAfter);

      if (!Number.isSafeInteger(after)) {
        return reply.status(400).send({
          error: {
            code:
              'COLLECTOR_INVALID_REQUEST',
            message:
              'after must be a non-negative integer',
          },
        });
      }

      try {
        const batch =
          await events.waitForEvents(
            after,
            25_000,
          );

        return {
          data: batch,
        };
      } catch (error) {
        if (
          error instanceof
          SequenceExpiredError
        ) {
          return reply.status(409).send({
            error: {
              code:
                'COLLECTOR_SEQUENCE_EXPIRED',
              message:
                'Requested event sequence is no longer available',
            },
          });
        }

        throw error;
      }
    },
  );

  return server;
}