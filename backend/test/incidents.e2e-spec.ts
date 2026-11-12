import type { NestExpressApplication } from '@nestjs/platform-express';
import { Test } from '@nestjs/testing';
import { ThrottlerGuard } from '@nestjs/throttler';
import { randomUUID } from 'node:crypto';
import { EMPTY } from 'rxjs';
import request from 'supertest';
import { AppModule } from '../src/app.module.js';
import { configureApp } from '../src/app.setup.js';
import { buildGraph } from '../src/modules/topology/build-graph.js';
import type { TopologySnapshot } from '../src/modules/topology/graph.types.js';
import { TopologyService } from '../src/modules/topology/topology.service.js';
import { PrismaService } from '../src/shared/prisma/prisma.service.js';
import { ENVIRONMENT_ID, api, db, web } from './topology/fixtures.js';

const password = 'password123';
const emails = ['incidents-e2e-a@example.com', 'incidents-e2e-b@example.com'];
const DB_NODE = 'svc:demo:db';

const snapshot: TopologySnapshot = {
  schemaVersion: 1,
  environmentId: ENVIRONMENT_ID,
  capturedAt: '2026-11-12T10:00:00.000Z',
  epoch: 'epoch-a',
  revision: 4,
  ...buildGraph(ENVIRONMENT_ID, [db(), api(), web()]),
};

describe('Incidents (e2e)', () => {
  let app: NestExpressApplication;
  let prisma: PrismaService;
  let current: TopologySnapshot | null;
  let ada: { id: string; cookie: string };
  let bob: { id: string; cookie: string };

  // Registers a user and logs in: its id and its session cookie.
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

  const create = (cookie: string, body: Record<string, unknown> = {}) =>
    http()
      .post('/api/v1/incidents')
      .set('Cookie', cookie)
      .send({
        title: 'API down',
        severity: 'sev2',
        environmentId: ENVIRONMENT_ID,
        seedNodeIds: [DB_NODE],
        ...body,
      });

  const patch = (cookie: string, id: string, body: Record<string, unknown>) =>
    http().patch(`/api/v1/incidents/${id}`).set('Cookie', cookie).send(body);

  const timeline = (cookie: string, id: string, query = '') =>
    http()
      .get(`/api/v1/incidents/${id}/timeline${query}`)
      .set('Cookie', cookie);

  const addNote = (cookie: string, id: string, body: unknown) =>
    http()
      .post(`/api/v1/incidents/${id}/notes`)
      .set('Cookie', cookie)
      .send({ body });

  const eventCount = (incidentId: string) =>
    prisma.incidentEvent.count({ where: { incidentId } });

  beforeAll(async () => {
    const moduleRef = await Test.createTestingModule({ imports: [AppModule] })
      .overrideGuard(ThrottlerGuard)
      .useValue({ canActivate: () => true })
      .overrideProvider(TopologyService)
      .useValue({
        changes$: EMPTY,
        status$: EMPTY,
        snapshot: () => current,
      })
      .compile();

    app = moduleRef.createNestApplication<NestExpressApplication>({
      bodyParser: false,
    });
    configureApp(app);
    await app.init();

    prisma = app.get(PrismaService);
    await clean();
  });

  beforeEach(async () => {
    current = snapshot;
    await clean();
    ada = await createUser(emails[0], 'Ada');
    bob = await createUser(emails[1], 'Bob');
  });

  afterAll(async () => {
    if (app) {
      await clean();
      await app.close();
    }
  });

  const routes: ['get' | 'post' | 'patch', string][] = [
    ['get', '/api/v1/incidents'],
    ['post', '/api/v1/incidents'],
    ['get', `/api/v1/incidents/${randomUUID()}`],
    ['patch', `/api/v1/incidents/${randomUUID()}`],
    ['get', `/api/v1/incidents/${randomUUID()}/timeline`],
    ['post', `/api/v1/incidents/${randomUUID()}/notes`],
  ];

  it.each(routes)('%s %s needs a session', async (method, url) => {
    const response = await http()[method](url).expect(401);

    expect(response.body.error.code).toBe('UNAUTHORIZED');
  });

  describe('POST /incidents', () => {
    it('opens an incident whose commander is its creator', async () => {
      const response = await create(ada.cookie).expect(201);
      const incident = response.body.data;

      expect(incident).toMatchObject({
        title: 'API down',
        severity: 'sev2',
        status: 'open',
        environmentId: ENVIRONMENT_ID,
        seedNodeIds: [DB_NODE],
        resolvedAt: null,
        commander: { id: ada.id, displayName: 'Ada', avatarUrl: null },
        version: 1,
        participants: [],
        runbook: [],
        myRole: null,
        latestAnalysis: null,
      });
      expect(incident.statusHistory).toEqual([
        { status: 'open', at: incident.startedAt },
      ]);
      expect(new Date(incident.startedAt).toISOString()).toBe(
        incident.startedAt,
      );
    });

    it('stores the topology it was opened against and a created event', async () => {
      const { id } = (await create(ada.cookie).expect(201)).body.data;

      expect(
        await prisma.incident.findUniqueOrThrow({
          where: { id },
          select: { openedAtEpoch: true, openedAtRevision: true },
        }),
      ).toEqual({ openedAtEpoch: 'epoch-a', openedAtRevision: 4 });
      expect(
        await prisma.incidentEvent.findMany({ where: { incidentId: id } }),
      ).toMatchObject([
        {
          type: 'created',
          version: 1,
          actorId: ada.id,
          data: {
            title: 'API down',
            severity: 'sev2',
            status: 'open',
            environmentId: ENVIRONMENT_ID,
            seedNodeIds: [DB_NODE],
            commanderId: ada.id,
          },
        },
      ]);
    });

    it('ignores a duplicate in seedNodeIds', async () => {
      const response = await create(ada.cookie, {
        seedNodeIds: [DB_NODE, DB_NODE],
      }).expect(201);

      expect(response.body.data.seedNodeIds).toEqual([DB_NODE]);
    });

    it('never shows an email', async () => {
      const { id } = (await create(ada.cookie).expect(201)).body.data;
      await patch(ada.cookie, id, { version: 1, commanderId: bob.id }).expect(
        200,
      );
      await addNote(ada.cookie, id, 'noted').expect(201);

      const bodies = await Promise.all([
        http().get('/api/v1/incidents').set('Cookie', bob.cookie),
        http().get(`/api/v1/incidents/${id}`).set('Cookie', bob.cookie),
        timeline(bob.cookie, id),
      ]);

      for (const response of bodies) {
        expect(JSON.stringify(response.body)).not.toMatch(/email|example\.com/);
      }
    });

    const invalid: [string, Record<string, unknown>, string][] = [
      ['an empty title', { title: '' }, 'title'],
      ['a blank title', { title: '   ' }, 'title'],
      ['a title of 201 characters', { title: 'x'.repeat(201) }, 'title'],
      ['a NUL character in the title', { title: 'a\0b' }, 'title'],
      ['an unknown severity', { severity: 'sev0' }, 'severity'],
      ['no environmentId', { environmentId: undefined }, 'environmentId'],
      ['no seed', { seedNodeIds: [] }, 'seedNodeIds'],
      [
        'more than 50 seeds',
        { seedNodeIds: Array.from({ length: 51 }, (_, i) => `svc:demo:${i}`) },
        'seedNodeIds',
      ],
      ['an unknown node', { seedNodeIds: ['svc:demo:nope'] }, 'seedNodeIds'],
      ['a property it does not accept', { status: 'resolved' }, 'status'],
    ];

    it.each(invalid)('refuses %s', async (_name, body, field) => {
      const response = await create(ada.cookie, body).expect(400);

      expect(response.body.error.code).toBe('VALIDATION_ERROR');
      expect(response.body.error.details).toHaveProperty(field);
      expect(await prisma.incident.count()).toBe(0);
    });

    it('names the unknown node', async () => {
      const response = await create(ada.cookie, {
        seedNodeIds: [DB_NODE, 'svc:demo:nope'],
      }).expect(400);

      expect(response.body.error.details.seedNodeIds).toEqual([
        'svc:demo:nope is not a node of the topology',
      ]);
    });

    it('needs a known topology, like the analyses', async () => {
      current = null;
      const unavailable = await create(ada.cookie).expect(503);

      expect(unavailable.body.error.code).toBe('TOPOLOGY_UNAVAILABLE');

      current = snapshot;
      const unknown = await create(ada.cookie, {
        environmentId: 'env_other',
      }).expect(404);

      expect(unknown.body.error.code).toBe('TOPOLOGY_UNKNOWN_ENVIRONMENT');
      expect(await prisma.incident.count()).toBe(0);
    });
  });

  describe('GET /incidents', () => {
    it('reads one incident, 404 when it does not exist', async () => {
      const created = (await create(ada.cookie).expect(201)).body.data;
      const read = await http()
        .get(`/api/v1/incidents/${created.id}`)
        .set('Cookie', bob.cookie)
        .expect(200);

      expect(read.body.data).toEqual(created);

      const missing = await http()
        .get(`/api/v1/incidents/${randomUUID()}`)
        .set('Cookie', bob.cookie)
        .expect(404);

      expect(missing.body.error.code).toBe('INCIDENT_NOT_FOUND');

      const notAnId = await http()
        .get('/api/v1/incidents/nope')
        .set('Cookie', bob.cookie)
        .expect(400);

      expect(notAnId.body.error.code).toBe('BAD_REQUEST');
    });

    it('lists the incidents, the newest first, and filters them', async () => {
      const first = (await create(ada.cookie, { title: 'first' }).expect(201))
        .body.data;
      const second = (
        await create(ada.cookie, { title: 'second', severity: 'sev1' }).expect(
          201,
        )
      ).body.data;

      current = { ...snapshot, environmentId: 'env_other' };
      const other = (
        await create(ada.cookie, {
          title: 'other',
          environmentId: 'env_other',
        }).expect(201)
      ).body.data;
      await patch(ada.cookie, first.id, {
        version: 1,
        status: 'investigating',
      }).expect(200);

      const list = (query: string) =>
        http().get(`/api/v1/incidents${query}`).set('Cookie', bob.cookie);
      const titles = async (query: string) =>
        (await list(query).expect(200)).body.data.map(
          (incident: { title: string }) => incident.title,
        );

      expect(await titles('')).toEqual(['other', 'second', 'first']);
      expect(await titles(`?environmentId=${ENVIRONMENT_ID}`)).toEqual([
        'second',
        'first',
      ]);
      expect(await titles('?environmentId=env_other')).toEqual(['other']);
      expect(await titles('?status=investigating')).toEqual(['first']);
      expect(await titles('?severity=sev1')).toEqual(['second']);
      expect(await titles('?environmentId=nope')).toEqual([]);

      // The summary of the contract: no version, no participants.
      const summary = (await list('?severity=sev1').expect(200)).body.data[0];

      expect(summary).toEqual({
        id: second.id,
        title: 'second',
        severity: 'sev1',
        status: 'open',
        environmentId: ENVIRONMENT_ID,
        startedAt: second.startedAt,
        resolvedAt: null,
        commander: { id: ada.id, displayName: 'Ada', avatarUrl: null },
        seedNodeIds: [DB_NODE],
      });
      expect(other.environmentId).toBe('env_other');
    });

    it.each(['?status=mitigated', '?severity=sev9', '?unknown=1'])(
      'refuses the filter %s',
      async (query) => {
        const response = await http()
          .get(`/api/v1/incidents${query}`)
          .set('Cookie', ada.cookie)
          .expect(400);

        expect(response.body.error.code).toBe('VALIDATION_ERROR');
      },
    );
  });

  describe('PATCH /incidents/:id', () => {
    it('changes the fields it is given and adds 1 to the version', async () => {
      const { id } = (await create(ada.cookie).expect(201)).body.data;
      const response = await patch(bob.cookie, id, {
        version: 1,
        status: 'investigating',
        severity: 'sev1',
      }).expect(200);

      expect(response.body.data).toMatchObject({
        status: 'investigating',
        severity: 'sev1',
        version: 2,
        resolvedAt: null,
      });
      expect(
        response.body.data.statusHistory.map(
          (entry: { status: string }) => entry.status,
        ),
      ).toEqual(['open', 'investigating']);
    });

    it('writes one event for each changed field, all with the new version', async () => {
      const { id } = (await create(ada.cookie).expect(201)).body.data;

      await patch(ada.cookie, id, {
        version: 1,
        title: 'API down (db)',
        status: 'investigating',
        commanderId: bob.id,
      }).expect(200);

      const events = await prisma.incidentEvent.findMany({
        where: { incidentId: id, type: { not: 'created' } },
        orderBy: { seq: 'asc' },
        select: { type: true, version: true, actorId: true, data: true },
      });

      expect(events).toEqual([
        {
          type: 'title_changed',
          version: 2,
          actorId: ada.id,
          data: { title: { from: 'API down', to: 'API down (db)' } },
        },
        {
          type: 'status_changed',
          version: 2,
          actorId: ada.id,
          data: { status: { from: 'open', to: 'investigating' } },
        },
        {
          type: 'commander_changed',
          version: 2,
          actorId: ada.id,
          data: { commanderId: { from: ada.id, to: bob.id } },
        },
      ]);
    });

    it('does nothing when every value is the current one', async () => {
      const { id } = (await create(ada.cookie).expect(201)).body.data;
      const response = await patch(ada.cookie, id, {
        version: 1,
        status: 'open',
        severity: 'sev2',
        title: 'API down',
      }).expect(200);

      expect(response.body.data.version).toBe(1);
      expect(await eventCount(id)).toBe(1);

      // Whatever version it sends: there is nothing to refuse.
      const old = await patch(ada.cookie, id, {
        version: 99,
        status: 'open',
      }).expect(200);

      expect(old.body.data.version).toBe(1);
      expect(await eventCount(id)).toBe(1);
    });

    it('dates resolvedAt from the last change to resolved, and reopens', async () => {
      const { id } = (await create(ada.cookie).expect(201)).body.data;
      const resolved = await patch(ada.cookie, id, {
        version: 1,
        status: 'resolved',
      }).expect(200);

      expect(resolved.body.data.resolvedAt).toBe(
        resolved.body.data.statusHistory[1].at,
      );

      const reopened = await patch(ada.cookie, id, {
        version: 2,
        status: 'open',
      }).expect(200);

      expect(reopened.body.data.resolvedAt).toBeNull();
      expect(reopened.body.data.statusHistory).toHaveLength(3);
    });

    const invalid: [string, Record<string, unknown>, string][] = [
      ['no version', { status: 'resolved' }, 'version'],
      ['a version of 0', { version: 0, status: 'resolved' }, 'version'],
      [
        'a version that is not a number',
        { version: '1', status: 'open' },
        'version',
      ],
      ['no field to change', { version: 1 }, 'body'],
      ['an unknown status', { version: 1, status: 'mitigated' }, 'status'],
      ['a null status', { version: 1, status: null }, 'status'],
      ['an unknown severity', { version: 1, severity: 'sev0' }, 'severity'],
      ['a blank title', { version: 1, title: ' ' }, 'title'],
      ['a NUL in the title', { version: 1, title: 'a\0' }, 'title'],
      [
        'a commanderId that is not a UUID',
        { version: 1, commanderId: 'x' },
        'commanderId',
      ],
      [
        'a commander that is not a user',
        { version: 1, commanderId: randomUUID() },
        'commanderId',
      ],
      [
        'seedNodeIds (not editable in v1)',
        { version: 1, seedNodeIds: [DB_NODE] },
        'seedNodeIds',
      ],
    ];

    it.each(invalid)('refuses %s', async (_name, body, field) => {
      const { id } = (await create(ada.cookie).expect(201)).body.data;
      const response = await patch(ada.cookie, id, body).expect(400);

      expect(response.body.error.code).toBe('VALIDATION_ERROR');
      expect(response.body.error.details).toHaveProperty(field);
      expect(await eventCount(id)).toBe(1);
    });

    it('answers 404 for an incident that does not exist', async () => {
      const response = await patch(ada.cookie, randomUUID(), {
        version: 1,
        status: 'resolved',
      }).expect(404);

      expect(response.body.error.code).toBe('INCIDENT_NOT_FOUND');
    });

    it('answers 409 with the current incident when the version is old', async () => {
      const { id } = (await create(ada.cookie).expect(201)).body.data;

      await patch(ada.cookie, id, { version: 1, severity: 'sev1' }).expect(200);

      // Bob still holds version 1 and asks for a value that is not the current one.
      const response = await patch(bob.cookie, id, {
        version: 1,
        status: 'resolved',
      }).expect(409);

      expect(response.body.error.code).toBe('INCIDENT_VERSION_CONFLICT');
      expect(response.body.error.details).toMatchObject({
        id,
        version: 2,
        severity: 'sev1',
        status: 'open',
      });
      expect(await eventCount(id)).toBe(2);
      expect(
        (await prisma.incident.findUniqueOrThrow({ where: { id } })).status,
      ).toBe('open');
    });

    it('lets one of two simultaneous updates through and refuses the other', async () => {
      const { id } = (await create(ada.cookie).expect(201)).body.data;

      // Different values: whichever runs second finds another version, so it
      // is the version that refuses it, never the "nothing to change" rule.
      const responses = await Promise.all([
        patch(ada.cookie, id, { version: 1, status: 'investigating' }),
        patch(bob.cookie, id, { version: 1, status: 'resolved' }),
      ]);
      const statuses = responses.map((response) => response.status).sort();

      expect(statuses).toEqual([200, 409]);

      const winner = responses.find((response) => response.status === 200)!;
      const loser = responses.find((response) => response.status === 409)!;

      expect(loser.body.error.code).toBe('INCIDENT_VERSION_CONFLICT');
      expect(loser.body.error.details.version).toBe(2);
      expect(loser.body.error.details.status).toBe(winner.body.data.status);
      expect(await eventCount(id)).toBe(2);
      expect(
        (await prisma.incident.findUniqueOrThrow({ where: { id } })).version,
      ).toBe(2);
    });
  });

  describe('timeline and notes', () => {
    it('tells everything that happened, in order', async () => {
      const { id } = (await create(ada.cookie).expect(201)).body.data;

      await patch(ada.cookie, id, {
        version: 1,
        status: 'investigating',
        severity: 'sev1',
      }).expect(200);
      await addNote(bob.cookie, id, 'Restarted the database').expect(201);
      await patch(bob.cookie, id, {
        version: 2,
        title: 'Database down',
        commanderId: bob.id,
      }).expect(200);
      await patch(bob.cookie, id, { version: 3, status: 'resolved' }).expect(
        200,
      );

      const entries = (await timeline(ada.cookie, id).expect(200)).body.data;

      expect(entries.map((entry: { kind: string }) => entry.kind)).toEqual([
        'action',
        'severity_change',
        'status_change',
        'note',
        'action',
        'action',
        'status_change',
      ]);
      expect(
        entries.map(
          (entry: { author: { displayName: string } }) =>
            entry.author.displayName,
        ),
      ).toEqual(['Ada', 'Ada', 'Ada', 'Bob', 'Bob', 'Bob', 'Bob']);

      const seqs = entries.map((entry: { seq: number }) => entry.seq);

      expect(seqs).toEqual([...seqs].sort((a, b) => a - b));
      expect(new Set(seqs).size).toBe(7);
      expect(entries[2]).toMatchObject({
        incidentId: id,
        summary: 'Ada changed the status from open to investigating',
        nodeIds: [],
        data: { kind: 'status_change', from: 'open', to: 'investigating' },
      });
      expect(entries[3]).toMatchObject({
        summary: 'Restarted the database',
        data: { kind: 'note' },
      });
      expect(entries[5].data).toEqual({
        kind: 'action',
        action: 'role_changed',
        userId: bob.id,
      });
    });

    it('sorts, filters by kind, and gives an empty list for a node', async () => {
      const { id } = (await create(ada.cookie).expect(201)).body.data;

      await patch(ada.cookie, id, {
        version: 1,
        status: 'investigating',
        severity: 'sev1',
      }).expect(200);
      await addNote(ada.cookie, id, 'hello').expect(201);

      const kinds = async (query: string) =>
        (await timeline(ada.cookie, id, query).expect(200)).body.data.map(
          (entry: { kind: string }) => entry.kind,
        );

      expect(await kinds('?order=desc')).toEqual([
        'note',
        'status_change',
        'severity_change',
        'action',
      ]);
      expect(await kinds('?kind=status_change,severity_change')).toEqual([
        'severity_change',
        'status_change',
      ]);
      expect(await kinds('?kind=note')).toEqual(['note']);
      expect(await kinds('?kind=infra_event,analysis')).toEqual([]);
      expect(await kinds(`?nodeId=${DB_NODE}`)).toEqual([]);

      for (const query of ['?kind=nope', '?kind=', '?order=up']) {
        const response = await timeline(ada.cookie, id, query).expect(400);

        expect(response.body.error.code).toBe('VALIDATION_ERROR');
      }
    });

    it('answers 404 for the timeline and the notes of an unknown incident', async () => {
      const unknown = randomUUID();

      expect(
        (await timeline(ada.cookie, unknown).expect(404)).body.error.code,
      ).toBe('INCIDENT_NOT_FOUND');
      expect(
        (await timeline(ada.cookie, unknown, '?nodeId=x').expect(404)).body
          .error.code,
      ).toBe('INCIDENT_NOT_FOUND');
      expect(
        (await addNote(ada.cookie, unknown, 'hello').expect(404)).body.error
          .code,
      ).toBe('INCIDENT_NOT_FOUND');
    });

    it('adds a note without changing the version, so it never fails an update', async () => {
      const { id } = (await create(ada.cookie).expect(201)).body.data;
      const note = await addNote(
        bob.cookie,
        id,
        '  Looking at the logs ',
      ).expect(201);

      expect(note.body.data).toMatchObject({
        incidentId: id,
        kind: 'note',
        summary: 'Looking at the logs',
        author: { id: bob.id, displayName: 'Bob' },
      });
      expect(
        (await prisma.incident.findUniqueOrThrow({ where: { id } })).version,
      ).toBe(1);

      // Ada read version 1 before the note: her update still goes through.
      await patch(ada.cookie, id, { version: 1, severity: 'sev1' }).expect(200);
      await addNote(bob.cookie, id, 'after').expect(201);

      const versions = await prisma.incidentEvent.findMany({
        where: { incidentId: id },
        orderBy: { seq: 'asc' },
        select: { type: true, version: true },
      });

      expect(versions).toEqual([
        { type: 'created', version: 1 },
        { type: 'note_added', version: 1 },
        { type: 'severity_changed', version: 2 },
        { type: 'note_added', version: 2 },
      ]);
    });

    const invalidNotes: [string, unknown][] = [
      ['an empty note', ''],
      ['a blank note', '   '],
      ['a note of 2001 characters', 'x'.repeat(2001)],
      ['a NUL character', 'a\0b'],
      ['a body that is not text', 12],
      ['no body', undefined],
    ];

    it.each(invalidNotes)('refuses %s', async (_name, body) => {
      const { id } = (await create(ada.cookie).expect(201)).body.data;
      const response = await addNote(ada.cookie, id, body).expect(400);

      expect(response.body.error.code).toBe('VALIDATION_ERROR');
      expect(response.body.error.details).toHaveProperty('body');
      expect(await eventCount(id)).toBe(1);
    });

    it('accepts a note of 2000 characters', async () => {
      const { id } = (await create(ada.cookie).expect(201)).body.data;

      await addNote(ada.cookie, id, 'x'.repeat(2000)).expect(201);
    });
  });
});
