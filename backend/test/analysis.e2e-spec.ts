import type { NestExpressApplication } from '@nestjs/platform-express';
import { Test } from '@nestjs/testing';
import { ThrottlerGuard } from '@nestjs/throttler';
import {
  type IncomingMessage,
  type Server,
  type ServerResponse,
  createServer,
} from 'node:http';
import type { AddressInfo } from 'node:net';
import { EMPTY } from 'rxjs';
import request from 'supertest';
import { AppModule } from '../src/app.module.js';
import { configureApp } from '../src/app.setup.js';
import type { AnalyzeRequest } from '../src/modules/analysis/graph-engine-client.js';
import { buildGraph } from '../src/modules/topology/build-graph.js';
import type { TopologySnapshot } from '../src/modules/topology/graph.types.js';
import { TopologyService } from '../src/modules/topology/topology.service.js';
import { PrismaService } from '../src/shared/prisma/prisma.service.js';
import { ENVIRONMENT_ID, api, db, web } from './topology/fixtures.js';

const email = 'analysis-e2e@example.com';
const password = 'password123';
const path = `/api/v1/environments/${ENVIRONMENT_ID}/analyses`;

const graph = buildGraph(ENVIRONMENT_ID, [db(), api(), web()]);

const snapshot: TopologySnapshot = {
  schemaVersion: 1,
  environmentId: ENVIRONMENT_ID,
  capturedAt: '2026-11-02T10:00:00.000Z',
  epoch: 'epoch-a',
  revision: 4,
  ...graph,
};

type Handler = (request: IncomingMessage, response: ServerResponse) => void;

describe('POST /environments/:environmentId/analyses (e2e)', () => {
  let app: NestExpressApplication;
  let prisma: PrismaService;
  let engine: Server;
  let handler: Handler;
  let cookie: string;
  let current: TopologySnapshot | null;
  const received: { url?: string; body: AnalyzeRequest }[] = [];
  const previousEngineUrl = process.env.GRAPH_ENGINE_URL;

  // The engine, as the contract describes it: it echoes the revision it is sent.
  const answer: Handler = (_request, response) => {
    const { topology } = received[received.length - 1].body;

    response.writeHead(200, { 'content-type': 'application/json' });
    response.end(
      JSON.stringify({
        data: {
          schemaVersion: 1,
          environmentId: topology.environmentId,
          topologyRevision: topology.topologyRevision,
          analyzedAt: '2026-11-02T10:00:01Z',
          results: { criticalNodes: { nodes: [] } },
        },
      }),
    );
  };

  const post = (body: unknown, url = path) =>
    request(app.getHttpServer())
      .post(url)
      .set('Cookie', cookie)
      .send(body as object);

  beforeAll(async () => {
    engine = createServer((incoming, response) => {
      let body = '';

      incoming.on('data', (chunk) => (body += chunk));
      incoming.on('end', () => {
        received.push({ url: incoming.url, body: JSON.parse(body) });
        handler(incoming, response);
      });
    });
    await new Promise<void>((resolve) =>
      engine.listen(0, '127.0.0.1', resolve),
    );

    // Read when the application is built.
    process.env.GRAPH_ENGINE_URL = `http://127.0.0.1:${(engine.address() as AddressInfo).port}`;

    const moduleRef = await Test.createTestingModule({ imports: [AppModule] })
      .overrideGuard(ThrottlerGuard)
      .useValue({ canActivate: () => true })
      .overrideProvider(TopologyService)
      .useValue({
        changes$: EMPTY,
        status$: EMPTY,
        snapshot: () => current,
        status: () => ({ stale: false, since: null }),
      })
      .compile();

    app = moduleRef.createNestApplication<NestExpressApplication>({
      bodyParser: false,
    });
    configureApp(app);
    await app.init();

    prisma = app.get(PrismaService);
    await prisma.user.deleteMany({ where: { email } });
    await request(app.getHttpServer())
      .post('/api/v1/auth/register')
      .send({ email, password })
      .expect(201);

    const login = await request(app.getHttpServer())
      .post('/api/v1/auth/login')
      .send({ email, password })
      .expect(200);

    cookie = login.get('Set-Cookie')?.[0].split(';')[0] ?? '';
  });

  afterAll(async () => {
    await prisma.user.deleteMany({ where: { email } });
    await app.close();
    engine.closeAllConnections();
    await new Promise((resolve) => engine.close(resolve));

    if (previousEngineUrl === undefined) {
      delete process.env.GRAPH_ENGINE_URL;
    } else {
      process.env.GRAPH_ENGINE_URL = previousEngineUrl;
    }
  });

  beforeEach(() => {
    current = snapshot;
    handler = answer;
    received.length = 0;
  });

  it('requires a session', async () => {
    const response = await request(app.getHttpServer())
      .post(path)
      .send({ analyses: ['critical_nodes'] })
      .expect(401);

    expect(response.body.error.code).toBe('UNAUTHORIZED');
    expect(received).toHaveLength(0);
  });

  it('analyses the current topology through the engine', async () => {
    const response = await post({
      analyses: ['critical_nodes', 'blast_radius'],
      seedNodeIds: ['svc:demo:db'],
      maxDepth: 3,
    }).expect(200);

    expect(response.body).toEqual({
      data: {
        environmentId: ENVIRONMENT_ID,
        topologyRevision: 'epoch-a:4',
        analyzedAt: '2026-11-02T10:00:01Z',
        freshness: 'current',
        results: { criticalNodes: { nodes: [] } },
      },
    });
    expect(received).toHaveLength(1);
    expect(received[0].url).toBe('/api/v1/analyze');
    expect(received[0].body.analyses).toEqual([
      'blast_radius',
      'critical_nodes',
    ]);
    expect(received[0].body.options).toEqual({
      seedNodeIds: ['svc:demo:db'],
      maxDepth: 3,
    });
    expect(received[0].body.topology).toMatchObject({
      environmentId: ENVIRONMENT_ID,
      topologyRevision: 'epoch-a:4',
      nodes: graph.nodes,
      edges: graph.edges,
    });
    // The engine would ignore these two and echo nothing.
    expect(received[0].body.topology).not.toHaveProperty('epoch');
    expect(received[0].body.topology).not.toHaveProperty('revision');
  });

  it.each([
    ['no analyses', {}],
    ['an empty list of analyses', { analyses: [] }],
    ['an unknown analysis', { analyses: ['nope'] }],
    ['a repeated analysis', { analyses: ['critical_nodes', 'critical_nodes'] }],
    ['a depth too large', { analyses: ['critical_nodes'], maxDepth: 11 }],
    [
      'a depth that is not an integer',
      { analyses: ['critical_nodes'], maxDepth: 1.5 },
    ],
    ['too many paths', { analyses: ['critical_nodes'], maxPaths: 101 }],
    ['an empty seed', { analyses: ['blast_radius'], seedNodeIds: [''] }],
    [
      'a field it does not know',
      { analyses: ['critical_nodes'], topology: {} },
    ],
  ])('refuses %s without calling the engine', async (_name, body) => {
    const response = await post(body).expect(400);

    expect(response.body.error.code).toBe('VALIDATION_ERROR');
    expect(received).toHaveLength(0);
  });

  it('passes on the error of the engine for a request it refuses', async () => {
    handler = (_request, response) => {
      response.writeHead(400, { 'content-type': 'application/json' });
      response.end(
        JSON.stringify({
          error: {
            code: 'GRAPH_MISSING_SEEDS',
            message: 'Analysis blast_radius requires options.seedNodeIds',
          },
        }),
      );
    };

    const response = await post({ analyses: ['blast_radius'] }).expect(400);

    expect(response.body).toEqual({
      error: {
        code: 'GRAPH_MISSING_SEEDS',
        message: 'Analysis blast_radius requires options.seedNodeIds',
      },
    });
  });

  it('answers 503 ANALYSIS_UNAVAILABLE when the engine drops the call', async () => {
    handler = (incoming) => incoming.socket.destroy();

    const response = await post({ analyses: ['critical_nodes'] }).expect(503);

    expect(response.body.error.code).toBe('ANALYSIS_UNAVAILABLE');
  });

  it('answers 404 for an environment it does not follow', async () => {
    const response = await post(
      { analyses: ['critical_nodes'] },
      '/api/v1/environments/env_other/analyses',
    ).expect(404);

    expect(response.body.error.code).toBe('TOPOLOGY_UNKNOWN_ENVIRONMENT');
    expect(received).toHaveLength(0);
  });

  it('answers 503 TOPOLOGY_UNAVAILABLE before the first topology', async () => {
    current = null;

    const response = await post({ analyses: ['critical_nodes'] }).expect(503);

    expect(response.body.error.code).toBe('TOPOLOGY_UNAVAILABLE');
    expect(received).toHaveLength(0);
  });
});
