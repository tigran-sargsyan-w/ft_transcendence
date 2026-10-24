import { createServer, type Server } from 'node:http';
import type { AddressInfo } from 'node:net';
import type { INestApplication } from '@nestjs/common';
import { Test } from '@nestjs/testing';
import { TopologyModule } from '../../src/modules/topology/topology.module.js';
import { TopologyService } from '../../src/modules/topology/topology.service.js';
import { snapshotAfterUp } from './fixtures.js';

// A collector that serves one snapshot and never has news.
describe('TopologyModule', () => {
  let server: Server;
  let url: string;
  let requests: string[];
  let app: INestApplication | undefined;
  const saved = process.env.COLLECTOR_URL;

  beforeAll(async () => {
    server = createServer((request, response) => {
      requests.push(request.url ?? '');

      if (request.url === '/api/v1/snapshot') {
        response.writeHead(200, { 'content-type': 'application/json' });
        response.end(JSON.stringify({ data: snapshotAfterUp }));
      }
      // The events: a long poll nothing answers.
    });

    await new Promise<void>((resolve) =>
      server.listen(0, '127.0.0.1', resolve),
    );

    url = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
  });

  afterAll(async () => {
    server.closeAllConnections();
    await new Promise((resolve) => server.close(resolve));
  });

  beforeEach(() => {
    requests = [];
  });

  afterEach(async () => {
    await app?.close();
    app = undefined;

    if (saved === undefined) {
      delete process.env.COLLECTOR_URL;
    } else {
      process.env.COLLECTOR_URL = saved;
    }
  });

  async function start() {
    const moduleRef = await Test.createTestingModule({
      imports: [TopologyModule],
    }).compile();

    app = moduleRef.createNestApplication();
    await app.init();

    return app.get(TopologyService);
  }

  it('builds the topology from the collector it is pointed at', async () => {
    process.env.COLLECTOR_URL = url;

    const service = await start();

    while (!service.snapshot()) {
      await new Promise((resolve) => setImmediate(resolve));
    }

    expect(service.snapshot()?.nodes.map((node) => node.id)).toContain(
      'svc:demo:api',
    );
    expect(requests).toContain('/api/v1/snapshot');
  });

  it('stops following the collector when the application closes', async () => {
    process.env.COLLECTOR_URL = url;

    await start();
    await app!.close();
    app = undefined;

    const before = requests.length;

    await new Promise((resolve) => setTimeout(resolve, 50));

    expect(requests.length).toBe(before);
  });

  it('builds nothing, and asks nothing, without COLLECTOR_URL', async () => {
    delete process.env.COLLECTOR_URL;

    const service = await start();

    await new Promise((resolve) => setTimeout(resolve, 50));

    expect(service.snapshot()).toBeNull();
    expect(requests).toEqual([]);
  });
});
