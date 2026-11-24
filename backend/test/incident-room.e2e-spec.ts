import type { NestExpressApplication } from '@nestjs/platform-express';
import { Test } from '@nestjs/testing';
import { ThrottlerGuard } from '@nestjs/throttler';
import type { AddressInfo } from 'node:net';
import { randomUUID } from 'node:crypto';
import { EMPTY } from 'rxjs';
import { io, type Socket } from 'socket.io-client';
import request from 'supertest';
import { AppModule } from '../src/app.module.js';
import { configureApp } from '../src/app.setup.js';
import { buildGraph } from '../src/modules/topology/build-graph.js';
import type { TopologySnapshot } from '../src/modules/topology/graph.types.js';
import { TopologyService } from '../src/modules/topology/topology.service.js';
import { PrismaService } from '../src/shared/prisma/prisma.service.js';
import { ENVIRONMENT_ID, api, db, web } from './topology/fixtures.js';

const password = 'password123';
// Their own addresses: the e2e files may run at the same time.
const emails = [
  'incident-room-e2e-a@example.com',
  'incident-room-e2e-b@example.com',
  'incident-room-e2e-c@example.com',
];

const snapshot: TopologySnapshot = {
  schemaVersion: 1,
  environmentId: ENVIRONMENT_ID,
  capturedAt: '2026-11-12T10:00:00.000Z',
  epoch: 'epoch-a',
  revision: 4,
  ...buildGraph(ENVIRONMENT_ID, [db(), api(), web()]),
};

type Envelope = { event: string; payload: any; timestamp: string };
type User = { id: string; cookie: string };

describe('Incident room (e2e)', () => {
  let app: NestExpressApplication;
  let prisma: PrismaService;
  let url: string;
  let ada: User;
  let bob: User;
  let cara: User;
  const sockets: Socket[] = [];

  async function createUser(email: string, displayName: string) {
    const registered = await request(app.getHttpServer())
      .post('/api/v1/auth/register')
      .send({ email, password, displayName })
      .expect(201);
    const login = await request(app.getHttpServer())
      .post('/api/v1/auth/login')
      .send({ email, password })
      .expect(200);

    return {
      id: registered.body.data.id as string,
      cookie: (login.get('Set-Cookie')?.[0] ?? '').split(';')[0],
    };
  }

  // The events and the incidents of the test users go first: the foreign keys
  // are RESTRICT.
  async function clean() {
    const createdByTestUser = { createdBy: { email: { in: emails } } };

    await prisma.incidentEvent.deleteMany({
      where: { incident: createdByTestUser },
    });
    await prisma.incident.deleteMany({ where: createdByTestUser });
    await prisma.user.deleteMany({ where: { email: { in: emails } } });
  }

  const http = () => request(app.getHttpServer());

  async function createIncident(user: User) {
    const response = await http()
      .post('/api/v1/incidents')
      .set('Cookie', user.cookie)
      .send({
        title: 'API down',
        severity: 'sev2',
        environmentId: ENVIRONMENT_ID,
        seedNodeIds: ['svc:demo:db'],
      })
      .expect(201);

    return response.body.data.id as string;
  }

  const patch = (user: User, id: string, body: Record<string, unknown>) =>
    http()
      .patch(`/api/v1/incidents/${id}`)
      .set('Cookie', user.cookie)
      .send(body);

  async function connect(user: User) {
    const socket = io(url, {
      transports: ['websocket'],
      reconnection: false,
      forceNew: true,
      extraHeaders: { cookie: user.cookie },
    });

    sockets.push(socket);

    await new Promise<void>((resolve, reject) => {
      socket.once('connect', resolve);
      socket.once('connect_error', reject);
    });

    return socket;
  }

  const emit = (socket: Socket, event: string, ...args: unknown[]) =>
    socket.timeout(2000).emitWithAck(event, ...args);

  const join = (socket: Socket, incidentId: string) =>
    emit(socket, 'incident:join', { incidentId });

  // Every message the server sends to this socket, in order.
  function collect(socket: Socket) {
    const received: Envelope[] = [];

    socket.onAny((_event, envelope: Envelope) => received.push(envelope));

    return received;
  }

  const names = (received: Envelope[]) => received.map(({ event }) => event);

  const waitFor = (received: Envelope[], count: number) =>
    vi.waitFor(() => expect(received).toHaveLength(count), { timeout: 2000 });

  // Sockets keep the order of what they receive. A note written after the
  // change comes after everything the change sent: once it is there, a message
  // sent twice would be in front of it. Returns, for each list, what came
  // before the note.
  async function untilNote(incidentId: string, ...lists: Envelope[][]) {
    const flush = await http()
      .post(`/api/v1/incidents/${incidentId}/notes`)
      .set('Cookie', ada.cookie)
      .send({ body: 'flush' })
      .expect(201);
    const flushId = flush.body.data.id as string;

    await Promise.all(
      lists.map((received) =>
        vi.waitFor(
          () => expect(received.at(-1)?.payload.entry?.id).toBe(flushId),
          { timeout: 2000 },
        ),
      ),
    );

    return lists.map((received) => received.slice(0, -1));
  }

  beforeAll(async () => {
    const moduleRef = await Test.createTestingModule({ imports: [AppModule] })
      .overrideGuard(ThrottlerGuard)
      .useValue({ canActivate: () => true })
      .overrideProvider(TopologyService)
      .useValue({
        changes$: EMPTY,
        status$: EMPTY,
        snapshot: () => snapshot,
      })
      .compile();

    app = moduleRef.createNestApplication<NestExpressApplication>({
      bodyParser: false,
    });
    configureApp(app);
    // A real port: the clients connect to it.
    await app.listen(0);

    const { port } = app.getHttpServer().address() as AddressInfo;

    url = `http://127.0.0.1:${port}`;
    prisma = app.get(PrismaService);
    await clean();
  });

  beforeEach(async () => {
    await clean();
    ada = await createUser(emails[0], 'Ada');
    bob = await createUser(emails[1], 'Bob');
    cara = await createUser(emails[2], 'Cara');
  });

  afterEach(() => {
    sockets.splice(0).forEach((socket) => socket.close());
  });

  afterAll(async () => {
    if (app) {
      await clean();
      await app.close();
    }
  });

  describe('incident:join and incident:leave', () => {
    it('answers a join with the id of the incident', async () => {
      const id = await createIncident(ada);
      const socket = await connect(bob);

      expect(await join(socket, id)).toEqual({ data: { incidentId: id } });
    });

    it('answers INCIDENT_NOT_FOUND for an incident that does not exist', async () => {
      const socket = await connect(bob);
      const answer = await join(socket, randomUUID());

      expect(answer.error.code).toBe('INCIDENT_NOT_FOUND');
    });

    it('answers a validation error, and keeps the connection, for a bad message', async () => {
      const id = await createIncident(ada);
      const socket = await connect(bob);

      for (const body of [
        { incidentId: 'inc_042' },
        { incidentId: id, room: 'env:demo' },
        {},
        'text',
      ]) {
        const answer = await emit(socket, 'incident:join', body);

        expect(answer.error.code).toBe('VALIDATION_ERROR');
        // The field is named, also for a message that is not an object.
        expect(Object.keys(answer.error.details).length).toBeGreaterThan(0);
      }

      expect(socket.connected).toBe(true);
      // Four refusals and one join: the limit is ten commands in ten seconds.
      expect(await join(socket, id)).toEqual({ data: { incidentId: id } });
    });

    it('answers a leave with an empty answer, also from a room the socket never joined', async () => {
      const socket = await connect(bob);

      expect(
        await emit(socket, 'incident:leave', { incidentId: randomUUID() }),
      ).toEqual({ data: {} });
    });

    it('refuses a leave with a bad message', async () => {
      const socket = await connect(bob);
      const answer = await emit(socket, 'incident:leave', {
        incidentId: 'inc_042',
      });

      expect(answer.error.code).toBe('VALIDATION_ERROR');
    });
  });

  describe('Presence and Incident Room integration', () => {
    it('keeps Presence and Incident Room working together', async () => {
      const id = await createIncident(ada);

      // Establish an accepted friendship.
      const friendRequest = await http()
        .post('/api/v1/users/me/friend-requests')
        .set('Cookie', ada.cookie)
        .send({ targetUserId: bob.id })
        .expect(201);

      await http()
        .post(`/api/v1/users/me/friend-requests/${friendRequest.body.data.id}/accept`)
        .set('Cookie', bob.cookie)
        .expect(200);

      const adaSocket = await connect(ada);
      const presenceEvents: Envelope[] = [];

      adaSocket.on('presence:changed', (event: Envelope) => {
        presenceEvents.push(event);
      });

      const bobSocket = await connect(bob);

      await vi.waitFor(() => {
        expect(presenceEvents.some((event) =>
          event.payload.userId === bob.id &&
          event.payload.isOnline === true,
        )).toBe(true);
      }, { timeout: 4000 });

      // Both friends join the same incident.
      expect(await join(adaSocket, id)).toEqual({
        data: { incidentId: id },
      });
      expect(await join(bobSocket, id)).toEqual({
        data: { incidentId: id },
      });

      const adaMessages = collect(adaSocket);
      const bobMessages = collect(bobSocket);
      const incidentMessages = (messages: Envelope[]) =>
        messages.filter((message) =>
          message.event === 'incident:updated' ||
          message.event === 'timeline:entry_added',
        );

      await patch(ada, id, {
        version: 1,
        title: 'Integrated update',
      }).expect(200);

      await vi.waitFor(() => {
        expect(incidentMessages(adaMessages)).toHaveLength(2);
        expect(incidentMessages(bobMessages)).toHaveLength(2);
      }, { timeout: 4000 });

      expect(incidentMessages(bobMessages).map((message) => message.event))
        .toEqual(['incident:updated', 'timeline:entry_added']);

      // Logout must disconnect Bob and notify his friend.
      const disconnected = new Promise<void>((resolve) => {
        bobSocket.once('disconnect', () => resolve());
      });

      await http()
        .post('/api/v1/auth/logout')
        .set('Cookie', bob.cookie)
        .expect(204);

      await disconnected;

      await vi.waitFor(() => {
        expect(presenceEvents.some((event) =>
          event.payload.userId === bob.id &&
          event.payload.isOnline === false,
        )).toBe(true);
      }, { timeout: 4000 });

      expect(adaSocket.connected).toBe(true);
      expect(bobSocket.connected).toBe(false);

      // Ada still receives updates; Bob's closed socket does not.
      await patch(ada, id, {
        version: 2,
        title: 'After logout',
      }).expect(200);

      await vi.waitFor(() => {
        expect(incidentMessages(adaMessages)).toHaveLength(4);
      }, { timeout: 4000 });

      expect(incidentMessages(bobMessages)).toHaveLength(2);
    });
  });

  describe('what the room receives', () => {
    it('sends every socket of the room the update, then its entries in the order of the timeline', async () => {
      const id = await createIncident(ada);
      const [forAda, forBob] = [await connect(ada), await connect(bob)];
      const [seenByAda, seenByBob] = [collect(forAda), collect(forBob)];

      await join(forAda, id);
      await join(forBob, id);

      await patch(ada, id, {
        version: 1,
        title: 'DB down',
        severity: 'sev1',
      }).expect(200);
      const [forAdaOnly, forBobOnly] = await untilNote(
        id,
        seenByAda,
        seenByBob,
      );

      // The same three messages for both, `updated` first, none twice.
      expect(forBobOnly).toEqual(forAdaOnly);
      expect(names(forBobOnly)).toEqual([
        'incident:updated',
        'timeline:entry_added',
        'timeline:entry_added',
      ]);

      const [updated, first, second] = forBobOnly;

      expect(updated.payload.incidentId).toBe(id);
      expect(updated.payload.incident).toMatchObject({
        id,
        version: 2,
        title: 'DB down',
        severity: 'sev1',
      });
      expect(updated.payload.incident.statusHistory).toHaveLength(1);
      expect(typeof updated.timestamp).toBe('string');
      expect(first.payload.incidentId).toBe(id);
      expect(first.payload.entry).toMatchObject({
        incidentId: id,
        author: { id: ada.id, displayName: 'Ada' },
      });
      expect(second.payload.entry.seq).toBeGreaterThan(first.payload.entry.seq);
      expect(first.payload.entry.data.action).toBe('title_changed');
      expect(second.payload.entry.data.kind).toBe('severity_change');
    });

    it('sends only the entry of a note: the incident did not change', async () => {
      const id = await createIncident(ada);
      const socket = await connect(bob);
      const seen = collect(socket);

      await join(socket, id);
      const note = await http()
        .post(`/api/v1/incidents/${id}/notes`)
        .set('Cookie', ada.cookie)
        .send({ body: 'Restarting the database' })
        .expect(201);

      const [before] = await untilNote(id, seen);

      expect(names(before)).toEqual(['timeline:entry_added']);
      expect(before[0].payload).toEqual({
        incidentId: id,
        entry: note.body.data,
      });
    });

    it('sends nothing to a socket that is not in the room, and its own room still works', async () => {
      const room = await createIncident(ada);
      const other = await createIncident(ada);
      const inside = await connect(bob);
      const outsider = await connect(cara);
      const [seenInside, seenOutsider] = [collect(inside), collect(outsider)];

      await join(inside, room);
      // The control: this socket does receive what happens in its own room.
      await join(outsider, other);

      await patch(ada, room, { version: 1, title: 'Room' }).expect(200);
      await patch(ada, other, { version: 1, title: 'Other' }).expect(200);
      await Promise.all([waitFor(seenInside, 2), waitFor(seenOutsider, 2)]);

      // The first request answered before the second was made, so a message of
      // `room` meant for the outsider would be here ahead of those of `other`.
      expect(
        seenOutsider
          .filter(({ event }) => event === 'incident:updated')
          .map(({ payload }) => payload.incidentId),
      ).toEqual([other]);
      expect(seenOutsider.map(({ payload }) => payload.incidentId)).toEqual([
        other,
        other,
      ]);
      expect(seenInside.map(({ payload }) => payload.incidentId)).toEqual([
        room,
        room,
      ]);
    });

    it('sends nothing for an update that changes nothing or fails with a 409', async () => {
      const id = await createIncident(ada);
      const socket = await connect(bob);
      const seen = collect(socket);

      await join(socket, id);

      // The same title: `200`, no event, no version.
      await patch(ada, id, { version: 1, title: 'API down' }).expect(200);
      // An old version: nothing is written.
      await patch(ada, id, { version: 7, title: 'Late' }).expect(409);
      // The control: the first message that comes is the one of this update.
      await patch(ada, id, { version: 1, status: 'investigating' }).expect(200);
      const [before] = await untilNote(id, seen);

      expect(names(before)).toEqual([
        'incident:updated',
        'timeline:entry_added',
      ]);
      expect(before[0].payload.incident.version).toBe(2);
    });

    it('sends nothing more to a socket that left, and keeps its other rooms', async () => {
      const left = await createIncident(ada);
      const kept = await createIncident(ada);
      const socket = await connect(bob);
      const seen = collect(socket);

      await join(socket, left);
      await join(socket, kept);
      await emit(socket, 'incident:leave', { incidentId: left });

      await patch(ada, left, { version: 1, title: 'Left' }).expect(200);
      await patch(ada, kept, { version: 1, title: 'Kept' }).expect(200);
      await waitFor(seen, 2);

      expect(seen.map(({ payload }) => payload.incidentId)).toEqual([
        kept,
        kept,
      ]);
    });

    it('treats an id in capital letters as the same incident: join, update and leave', async () => {
      const id = await createIncident(ada);
      const other = await createIncident(ada);
      const capitals = id.toUpperCase();
      const socket = await connect(bob);
      const seen = collect(socket);

      expect(await join(socket, capitals)).toEqual({
        data: { incidentId: id },
      });

      // The URL in capitals too: the room is named after the stored id.
      await patch(ada, capitals, { version: 1, title: 'Capitals' }).expect(200);
      await waitFor(seen, 2);

      expect(seen.map(({ payload }) => payload.incidentId)).toEqual([id, id]);
      expect(seen[0].payload.incident.title).toBe('Capitals');

      // A leave in capitals really leaves: the control is the other incident.
      await emit(socket, 'incident:leave', { incidentId: capitals });
      await join(socket, other);
      await patch(ada, id, { version: 2, title: 'Left' }).expect(200);
      await patch(ada, other, { version: 1, title: 'Other' }).expect(200);
      await waitFor(seen, 4);

      expect(seen.slice(2).map(({ payload }) => payload.incidentId)).toEqual([
        other,
        other,
      ]);
    });

    it('sends nothing from before the join: the client reads that over REST', async () => {
      const id = await createIncident(ada);
      const socket = await connect(bob);
      const seen = collect(socket);

      await patch(ada, id, { version: 1, title: 'Before' }).expect(200);
      await join(socket, id);
      await patch(ada, id, { version: 2, title: 'After' }).expect(200);
      await waitFor(seen, 2);

      // Nothing from before the join: the client reads that over REST.
      expect(seen[0].payload.incident.version).toBe(3);
    });
  });
});
