import Fastify from 'fastify';
import { DockerClient } from './docker-client.js';

const PORT = Number(process.env.PORT ?? 3001);
const HOST = process.env.HOST ?? '0.0.0.0';
const DOCKER_API_URL =
  process.env.DOCKER_API_URL ?? 'http://docker-proxy:2375';

const server = Fastify({
  logger: true,
});

const docker = new DockerClient(DOCKER_API_URL);

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