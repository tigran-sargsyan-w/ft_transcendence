import { createApp } from './app.js';
import { DockerClient } from './docker-client.js';
import { EventStore } from './event-store.js';
import { EventWatcher } from './event-watcher.js';
import { SnapshotService } from './snapshot.js';

const PORT =
  Number(process.env.PORT ?? 3001);

const HOST =
  process.env.HOST ?? '0.0.0.0';

const DOCKER_API_URL =
  process.env.DOCKER_API_URL ??
  'http://docker-proxy:2375';

const ENVIRONMENT_ID =
  process.env.ENVIRONMENT_ID ??
  'env_local_compose';

const docker =
  new DockerClient(DOCKER_API_URL);

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

const server = createApp({
  docker,
  events,
  snapshots,
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