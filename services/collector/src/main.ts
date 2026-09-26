import Fastify from 'fastify';

import { DockerClient } from './docker-client.js';
import { SnapshotService } from './snapshot.js';

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

const snapshots = new SnapshotService(
  docker,
  ENVIRONMENT_ID,
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

async function start(): Promise<void> {
  try {
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