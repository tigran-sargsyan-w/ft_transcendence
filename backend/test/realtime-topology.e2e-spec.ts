import type { NestExpressApplication } from '@nestjs/platform-express';
import { Test } from '@nestjs/testing';
import { ThrottlerGuard } from '@nestjs/throttler';
import type { AddressInfo } from 'node:net';
import { Subject } from 'rxjs';
import { io, type Socket } from 'socket.io-client';
import request from 'supertest';
import { AppModule } from '../src/app.module.js';
import { configureApp } from '../src/app.setup.js';
import { RealtimeGateway } from '../src/modules/realtime/realtime.gateway.js';
import { buildGraph } from '../src/modules/topology/build-graph.js';
import type { CollectorClient } from '../src/modules/topology/collector-client.js';
import type { EventBatch } from '../src/modules/topology/collector.types.js';
import type {
  GraphEdge,
  GraphNode,
  TopologyDelta,
  TopologySnapshot,
} from '../src/modules/topology/graph.types.js';
import {
  type TopologyChange,
  TopologyService,
  type TopologyStatus,
} from '../src/modules/topology/topology.service.js';
import { PrismaService } from '../src/shared/prisma/prisma.service.js';
import {
  ENVIRONMENT_ID,
  STREAM_ID,
  api,
  db,
  emptySnapshot,
  eventsCrashAndRecreate,
  eventsUp,
  snapshotAfterRecreate,
  web,
} from './topology/fixtures.js';

const email = 'realtime-topology-e2e@example.com';
const password = 'password123';

type Envelope<T = unknown> = { event: string; payload: T; timestamp: string };

const sleep = (ms: number) =>
  new Promise<void>((resolve) => setTimeout(resolve, ms));

async function waitUntil(condition: () => boolean, timeoutMs = 3000) {
  const deadline = Date.now() + timeoutMs;

  while (!condition()) {
    if (Date.now() > deadline) {
      throw new Error('waitUntil timed out');
    }

    await sleep(5);
  }
}

// Boots the application on a real port, with a registered and logged in user.
async function boot(topology: unknown) {
  const moduleRef = await Test.createTestingModule({ imports: [AppModule] })
    .overrideGuard(ThrottlerGuard)
    .useValue({ canActivate: () => true })
    .overrideProvider(TopologyService)
    .useValue(topology)
    .compile();

  const app = moduleRef.createNestApplication<NestExpressApplication>({
    bodyParser: false,
  });

  configureApp(app);
  await app.listen(0);

  const { port } = app.getHttpServer().address() as AddressInfo;
  const prisma = app.get(PrismaService);

  await prisma.user.deleteMany({ where: { email } });
  await request(app.getHttpServer())
    .post('/api/v1/auth/register')
    .send({ email, password })
    .expect(201);

  const login = await request(app.getHttpServer())
    .post('/api/v1/auth/login')
    .send({ email, password })
    .expect(200);

  const cookie = login.get('Set-Cookie')?.[0] ?? '';

  return {
    app,
    prisma,
    url: `http://127.0.0.1:${port}`,
    token: cookie.split(';')[0].replace('sid=', ''),
  };
}

type Server = Awaited<ReturnType<typeof boot>>;

const sockets: Socket[] = [];

afterEach(() => {
  sockets.splice(0).forEach((socket) => socket.close());
});

async function connect({ url, token }: Server) {
  const socket = io(url, {
    transports: ['websocket'],
    reconnection: false,
    forceNew: true,
    extraHeaders: { cookie: `sid=${token}` },
  });

  sockets.push(socket);

  await new Promise<void>((resolve, reject) => {
    socket.once('connect', resolve);
    socket.once('connect_error', reject);
  });

  return socket;
}

// The answer of a command: what the server sends to the acknowledgement.
const command = (socket: Socket, event: string, ...args: unknown[]) =>
  socket.timeout(2000).emitWithAck(event, ...args);

// Every message the server sends to this socket, in order.
function collect(socket: Socket) {
  const received: Envelope[] = [];

  socket.onAny((_event, envelope: Envelope) => received.push(envelope));

  return received;
}

// Stands in for TopologyService: the test decides what the gateway relays.
function fakeTopology() {
  const changes = new Subject<TopologyChange>();
  const statuses = new Subject<TopologyStatus>();
  let snapshot: TopologySnapshot | null = null;
  let status: TopologyStatus = { stale: false, since: null };

  return {
    changes$: changes.asObservable(),
    status$: statuses.asObservable(),
    snapshot: () => snapshot,
    status: () => status,
    load(next: TopologySnapshot | null) {
      snapshot = next;
    },
    publish(change: TopologyChange) {
      if (change.type === 'snapshot') {
        snapshot = change.snapshot;
      }

      changes.next(change);
    },
    publishStatus(next: TopologyStatus) {
      status = next;
      statuses.next(next);
    },
  };
}

const graph = buildGraph(ENVIRONMENT_ID, [db(), api(), web()]);

const snapshotAt = (revision: number): TopologySnapshot => ({
  schemaVersion: 1,
  environmentId: ENVIRONMENT_ID,
  capturedAt: '2026-10-27T10:00:00.000Z',
  epoch: 'epoch-a',
  revision,
  ...graph,
});

const deltaFrom = (baseRevision: number): TopologyDelta => ({
  epoch: 'epoch-a',
  baseRevision,
  revision: baseRevision + 1,
  operations: [{ op: 'node.remove', id: 'svc:demo:web' }],
});

describe('Realtime topology commands (e2e)', () => {
  const topology = fakeTopology();
  let server: Server;

  beforeAll(async () => {
    server = await boot(topology);
  });

  beforeEach(() => {
    topology.load(snapshotAt(1));
  });

  afterAll(async () => {
    await server.prisma.user.deleteMany({ where: { email } });
    await server.app.close();
  });

  it('answers a subscription with the ack, then the snapshot, then the status', async () => {
    const socket = await connect(server);
    const log: string[] = [];

    socket.on('topology:snapshot', () => log.push('snapshot'));
    socket.on('topology:status', () => log.push('status'));
    socket.emit('topology:subscribe', {}, () => log.push('ack'));

    await waitUntil(() => log.length === 3);

    expect(log).toEqual(['ack', 'snapshot', 'status']);
  });

  it('sends the snapshot and the status in the envelope', async () => {
    const socket = await connect(server);
    const received = collect(socket);

    expect(
      await command(socket, 'topology:subscribe', {
        environmentId: ENVIRONMENT_ID,
      }),
    ).toEqual({ data: { environmentId: ENVIRONMENT_ID } });

    await waitUntil(() => received.length === 2);

    expect(received[0]).toEqual({
      event: 'topology:snapshot',
      payload: snapshotAt(1),
      timestamp: expect.stringMatching(/^\d{4}-\d\d-\d\dT[\d:.]+Z$/),
    });
    expect(received[1]).toMatchObject({
      event: 'topology:status',
      payload: { stale: false, since: null },
    });
  });

  it('subscribes without any argument', async () => {
    const socket = await connect(server);

    expect(await command(socket, 'topology:subscribe')).toEqual({
      data: { environmentId: ENVIRONMENT_ID },
    });
  });

  it('refuses a subscription while there is no topology', async () => {
    topology.load(null);

    const socket = await connect(server);

    expect(await command(socket, 'topology:subscribe', {})).toEqual({
      error: {
        code: 'TOPOLOGY_UNAVAILABLE',
        message: 'The topology is not available yet',
      },
    });
  });

  it('refuses an environment the server does not follow', async () => {
    const socket = await connect(server);

    expect(
      await command(socket, 'topology:subscribe', {
        environmentId: 'env_other',
      }),
    ).toEqual({
      error: {
        code: 'TOPOLOGY_UNKNOWN_ENVIRONMENT',
        message: 'Unknown environment',
      },
    });
  });

  it('answers an invalid message with a validation error and keeps the connection', async () => {
    const socket = await connect(server);

    expect(
      await command(socket, 'topology:subscribe', { environmentId: '' }),
    ).toEqual({
      error: {
        code: 'VALIDATION_ERROR',
        message: 'Request validation failed',
        details: { environmentId: ['environmentId should not be empty'] },
      },
    });
    expect(
      await command(socket, 'topology:subscribe', { colour: 'red' }),
    ).toMatchObject({
      error: {
        code: 'VALIDATION_ERROR',
        details: { colour: ['property colour should not exist'] },
      },
    });
    expect(
      await command(socket, 'topology:subscribe', 'not an object'),
    ).toMatchObject({
      error: {
        code: 'VALIDATION_ERROR',
        details: { body: ['body must be an object'] },
      },
    });

    expect(socket.connected).toBe(true);
    expect(await command(socket, 'topology:subscribe', {})).toEqual({
      data: { environmentId: ENVIRONMENT_ID },
    });
  });

  it('resyncs the socket that asks, and only that one', async () => {
    const asking = await connect(server);
    const other = await connect(server);
    const fromAsking = collect(asking);
    const fromOther = collect(other);

    await command(asking, 'topology:subscribe', {});
    await command(other, 'topology:subscribe', {});
    await waitUntil(() => fromAsking.length === 2 && fromOther.length === 2);

    topology.load(snapshotAt(2));

    expect(await command(asking, 'topology:resync')).toEqual({
      data: { environmentId: ENVIRONMENT_ID },
    });
    await waitUntil(() => fromAsking.length === 4);
    await sleep(50);

    expect(fromAsking.slice(2).map((message) => message.event)).toEqual([
      'topology:snapshot',
      'topology:status',
    ]);
    expect(fromAsking[2].payload).toMatchObject({ revision: 2 });
    expect(fromOther).toHaveLength(2);
  });

  it('refuses a resync before a subscription', async () => {
    const socket = await connect(server);

    expect(await command(socket, 'topology:resync')).toEqual({
      error: {
        code: 'TOPOLOGY_NOT_SUBSCRIBED',
        message: 'Not subscribed to the topology',
      },
    });
  });

  it('relays a change, a snapshot and a status to the subscribed sockets only', async () => {
    const subscribed = await connect(server);
    const bystander = await connect(server);
    const fromSubscribed = collect(subscribed);
    const fromBystander = collect(bystander);

    await command(subscribed, 'topology:subscribe', {});
    await waitUntil(() => fromSubscribed.length === 2);

    topology.publish({ type: 'delta', delta: deltaFrom(1) });
    topology.publish({ type: 'snapshot', snapshot: snapshotAt(5) });
    topology.publishStatus({ stale: true, since: '2026-10-27T10:00:00.000Z' });

    await waitUntil(() => fromSubscribed.length === 5);
    await sleep(50);

    expect(fromSubscribed.slice(2)).toEqual([
      expect.objectContaining({
        event: 'topology:delta',
        payload: deltaFrom(1),
      }),
      expect.objectContaining({
        event: 'topology:snapshot',
        payload: snapshotAt(5),
      }),
      expect.objectContaining({
        event: 'topology:status',
        payload: { stale: true, since: '2026-10-27T10:00:00.000Z' },
      }),
    ]);
    expect(fromBystander).toEqual([]);
  });

  it('stops relaying once the socket unsubscribed', async () => {
    const socket = await connect(server);
    const received = collect(socket);

    await command(socket, 'topology:subscribe', {});
    await waitUntil(() => received.length === 2);

    expect(await command(socket, 'topology:unsubscribe')).toEqual({ data: {} });

    topology.publish({ type: 'delta', delta: deltaFrom(1) });
    await sleep(50);

    expect(received).toHaveLength(2);
  });

  it('accepts an unsubscribe from a socket that never subscribed', async () => {
    const socket = await connect(server);

    expect(await command(socket, 'topology:unsubscribe')).toEqual({ data: {} });
  });

  it('refuses the eleventh command in ten seconds', async () => {
    const socket = await connect(server);

    for (let count = 0; count < 10; count += 1) {
      expect(await command(socket, 'topology:unsubscribe')).toEqual({
        data: {},
      });
    }

    expect(await command(socket, 'topology:unsubscribe')).toEqual({
      error: { code: 'TOO_MANY_REQUESTS', message: 'Too many requests' },
    });
  });

  it('closes the connection on a message over 16 KB', async () => {
    const socket = await connect(server);
    const closed = new Promise<void>((resolve) =>
      socket.once('disconnect', () => resolve()),
    );

    socket.emit('topology:subscribe', {
      environmentId: 'x'.repeat(20 * 1024),
    });
    await closed;

    expect(socket.connected).toBe(false);
  });
});

// The events of the demo project, one batch each, once the gate is open.
class ScriptedCollector implements CollectorClient {
  private readonly batches: EventBatch[] = [
    ...eventsUp,
    ...eventsCrashAndRecreate,
  ].map((event) => ({ streamId: STREAM_ID, events: [event] }));

  constructor(private readonly gate: Promise<void>) {}

  get left() {
    return this.batches.length;
  }

  async snapshot() {
    return emptySnapshot;
  }

  async events(_after: number, signal: AbortSignal) {
    await this.gate;

    const next = this.batches.shift();

    if (next) {
      return next;
    }

    return new Promise<EventBatch>((_resolve, reject) => {
      signal.addEventListener('abort', () => reject(signal.reason), {
        once: true,
      });
    });
  }
}

// What a client does with what it receives (docs/architecture/realtime-topology.md):
// a snapshot replaces its graph, a delta applies only when it follows.
function follow(socket: Socket) {
  const view = {
    epoch: '',
    revision: 0,
    nodes: new Map<string, GraphNode>(),
    edges: new Map<string, GraphEdge>(),
    snapshots: 0,
    gaps: 0,
  };

  socket.on('topology:snapshot', ({ payload }: Envelope<TopologySnapshot>) => {
    view.epoch = payload.epoch;
    view.revision = payload.revision;
    view.nodes = new Map(payload.nodes.map((node) => [node.id, node]));
    view.edges = new Map(payload.edges.map((edge) => [edge.id, edge]));
    view.snapshots += 1;
  });

  socket.on('topology:delta', ({ payload }: Envelope<TopologyDelta>) => {
    // Before its snapshot a client ignores what it receives.
    if (view.snapshots === 0) {
      return;
    }

    if (
      payload.epoch !== view.epoch ||
      payload.baseRevision !== view.revision
    ) {
      view.gaps += 1;
      return;
    }

    for (const operation of payload.operations) {
      if (operation.op === 'node.upsert') {
        view.nodes.set(operation.node.id, operation.node);
      } else if (operation.op === 'node.remove') {
        view.nodes.delete(operation.id);
      } else if (operation.op === 'edge.upsert') {
        view.edges.set(operation.edge.id, operation.edge);
      } else {
        view.edges.delete(operation.id);
      }
    }

    view.revision = payload.revision;
  });

  return view;
}

const byId = <T extends { id: string }>(items: Map<string, T>) =>
  [...items.values()].sort((a, b) => (a.id < b.id ? -1 : 1));

describe('Realtime topology under a burst (e2e)', () => {
  it('gives every client that subscribes the final graph, with no gap in the revisions', async () => {
    let release!: () => void;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    const collector = new ScriptedCollector(gate);
    const service = new TopologyService(collector, {
      retryMinMs: 10,
      retryMaxMs: 40,
      batchWindowMs: 15,
      staleAfterMs: 10_000,
    });
    const server = await boot(service);

    try {
      await waitUntil(() => service.snapshot() !== null);

      // Joining a room takes a few milliseconds, as with an adapter that
      // has to ask another process.
      const { adapter } = server.app.get(RealtimeGateway).server.of('/');
      const addAll = adapter.addAll.bind(adapter);

      adapter.addAll = async (id, rooms) => {
        await sleep(Math.random() * 15);
        return addAll(id, rooms);
      };

      const clients = await Promise.all(
        Array.from({ length: 30 }, () => connect(server)),
      );
      const views = clients.map(follow);

      // The burst starts, and the clients subscribe while it goes on.
      release();

      const answers = await Promise.all(
        clients.map(async (client) => {
          await sleep(Math.random() * 150);

          return command(client, 'topology:subscribe', {});
        }),
      );

      await waitUntil(() => collector.left === 0);
      await sleep(200);

      const final = service.snapshot();

      if (!final) {
        throw new Error('the topology has gone');
      }

      // The burst did happen, and ran to its end.
      expect(final.revision).toBeGreaterThan(5);
      expect(final.nodes).toEqual(
        buildGraph(ENVIRONMENT_ID, snapshotAfterRecreate.containers).nodes,
      );
      expect(answers.every((answer) => 'data' in answer)).toBe(true);

      for (const view of views) {
        expect(view.gaps).toBe(0);
        expect(view.revision).toBe(final.revision);
        expect(byId(view.nodes)).toEqual(final.nodes);
        expect(byId(view.edges)).toEqual(final.edges);
      }
    } finally {
      await server.prisma.user.deleteMany({ where: { email } });
      await server.app.close();
    }
  }, 15_000);
});
