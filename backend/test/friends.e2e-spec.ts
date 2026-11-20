import { randomUUID } from 'node:crypto';
import type { NestExpressApplication } from '@nestjs/platform-express';
import { Test } from '@nestjs/testing';
import { ThrottlerGuard } from '@nestjs/throttler';
import request from 'supertest';
import { AppModule } from '../src/app.module.js';
import { configureApp } from '../src/app.setup.js';
import { PrismaService } from '../src/shared/prisma/prisma.service.js';

describe('Friendships (e2e)', () => {
  let app: NestExpressApplication;
  let prisma: PrismaService;

  const password = 'password123';
  const emails = [
    'friends-e2e-ada@example.com',
    'friends-e2e-bob@example.com',
    'friends-e2e-carol@example.com',
  ];

  type Account = { id: string; cookie: string };
  let ada: Account;
  let bob: Account;
  let carol: Account;

  const http = () => request(app.getHttpServer());

  async function register(email: string, displayName: string) {
    const created = await http()
      .post('/api/v1/auth/register')
      .send({ email, displayName, password })
      .expect(201);
    const login = await http()
      .post('/api/v1/auth/login')
      .send({ email, password })
      .expect(200);

    return {
      id: created.body.data.id as string,
      cookie: (login.get('Set-Cookie')?.[0] ?? '').split(';')[0],
    };
  }

  async function clean() {
    // The new friendship FKs cascade when the test users are removed.
    await prisma.user.deleteMany({ where: { email: { in: emails } } });
  }

  const friendCount = () =>
    prisma.friendship.count({
      where: {
        OR: [
          { userLowId: { in: [ada.id, bob.id, carol.id] } },
          { userHighId: { in: [ada.id, bob.id, carol.id] } },
        ],
      },
    });

  const send = (who: Account, targetUserId: string) =>
    http()
      .post('/api/v1/users/me/friend-requests')
      .set('Cookie', who.cookie)
      .send({ targetUserId });

  const accept = (who: Account, id: string) =>
    http()
      .post('/api/v1/users/me/friend-requests/' + id + '/accept')
      .set('Cookie', who.cookie);

  const requests = (who: Account) =>
    http().get('/api/v1/users/me/friend-requests').set('Cookie', who.cookie);

  const friends = (who: Account) =>
    http().get('/api/v1/users/me/friends').set('Cookie', who.cookie);

  const deleteRequest = (who: Account, id: string) =>
    http()
      .delete('/api/v1/users/me/friend-requests/' + id)
      .set('Cookie', who.cookie);

  const removeFriend = (who: Account, id: string) =>
    http()
      .delete('/api/v1/users/me/friends/' + id)
      .set('Cookie', who.cookie);

  beforeAll(async () => {
    const moduleRef = await Test.createTestingModule({
      imports: [AppModule],
    })
      .overrideGuard(ThrottlerGuard)
      .useValue({ canActivate: () => true })
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
    await clean();
    ada = await register(emails[0], 'Ada');
    bob = await register(emails[1], 'Bob');
    carol = await register(emails[2], 'Carol');
  });

  afterAll(async () => {
    if (app) {
      await clean();
      await app.close();
    }
  });

  it('protects every friends endpoint with the session guard', async () => {
    const id = randomUUID();
    const paths: [string, 'get' | 'post' | 'delete'][] = [
      ['/api/v1/users/me/friends', 'get'],
      ['/api/v1/users/me/friend-requests', 'get'],
      ['/api/v1/users/me/friend-requests', 'post'],
      ['/api/v1/users/me/friend-requests/' + id + '/accept', 'post'],
      ['/api/v1/users/me/friend-requests/' + id, 'delete'],
      ['/api/v1/users/me/friends/' + id, 'delete'],
    ];

    for (const [path, method] of paths) {
      const response = await http()[method](path).expect(401);
      expect(response.body.error.code).toBe('UNAUTHORIZED');
    }
  });

  it('creates one pending request and shows only public user fields', async () => {
    const created = await send(ada, bob.id).expect(201);
    const id = created.body.data.id as string;

    expect(created.body.data).toMatchObject({
      id,
      status: 'pending',
      direction: 'outgoing',
      user: { id: bob.id, displayName: 'Bob', avatarUrl: null },
    });
    expect(created.body.data).not.toHaveProperty('email');

    const outgoing = (await requests(ada).expect(200)).body.data;
    const incoming = (await requests(bob).expect(200)).body.data;

    expect(outgoing.incoming).toEqual([]);
    expect(outgoing.outgoing).toMatchObject([
      { id, direction: 'outgoing', user: { id: bob.id } },
    ]);
    expect(incoming.outgoing).toEqual([]);
    expect(incoming.incoming).toMatchObject([
      { id, direction: 'incoming', user: { id: ada.id } },
    ]);

    expect((await requests(carol).expect(200)).body.data).toEqual({
      incoming: [],
      outgoing: [],
    });
    expect((await friends(ada).expect(200)).body.data).toEqual([]);
    expect(JSON.stringify([created.body, outgoing, incoming])).not.toContain(
      '@example.com',
    );
  });

  it('rejects requests to oneself, missing users and malformed bodies', async () => {
    const me = await send(ada, ada.id).expect(400);
    expect(me.body.error.details).toHaveProperty('targetUserId');

    const unknown = await send(ada, randomUUID()).expect(404);
    expect(unknown.body.error.code).toBe('USER_NOT_FOUND');

    for (const body of [
      {},
      { targetUserId: 'not-a-uuid' },
      { targetUserId: bob.id, isAdmin: true },
    ]) {
      const response = await http()
        .post('/api/v1/users/me/friend-requests')
        .set('Cookie', ada.cookie)
        .send(body)
        .expect(400);

      expect(response.body.error.code).toBe('VALIDATION_ERROR');
    }

    expect(await friendCount()).toBe(0);
  });

  it('rejects duplicate and reverse requests, even when submitted concurrently', async () => {
    const [first, second] = await Promise.all([
      send(ada, bob.id),
      send(bob, ada.id),
    ]);

    expect([first.status, second.status].sort()).toEqual([201, 409]);
    expect(
      (first.status === 409 ? first : second).body.error.code,
    ).toBe('FRIENDSHIP_ALREADY_EXISTS');
    expect(await friendCount()).toBe(1);

    const again = await send(ada, bob.id).expect(409);
    expect(again.body.error.code).toBe('FRIENDSHIP_ALREADY_EXISTS');
  });

  it('only lets the recipient accept, atomically and once', async () => {
    const id = (await send(ada, bob.id).expect(201)).body.data.id as string;

    const sender = await accept(ada, id).expect(404);
    expect(sender.body.error.code).toBe('FRIEND_REQUEST_NOT_FOUND');

    await accept(carol, id).expect(404);

    const [first, second] = await Promise.all([
      accept(bob, id),
      accept(bob, id),
    ]);
    expect([first.status, second.status].sort()).toEqual([200, 404]);

    const accepted = first.status === 200 ? first : second;
    expect(accepted.body.data).toMatchObject({
      id,
      status: 'accepted',
      user: { id: ada.id, displayName: 'Ada', avatarUrl: null },
    });
    expect(accepted.body.data.acceptedAt).toBeTruthy();

    expect((await friends(ada).expect(200)).body.data).toMatchObject([
      { id, user: { id: bob.id }, friendsSince: accepted.body.data.acceptedAt },
    ]);
    expect((await friends(bob).expect(200)).body.data).toMatchObject([
      { id, user: { id: ada.id } },
    ]);
    expect((await friends(carol).expect(200)).body.data).toEqual([]);
    expect((await requests(ada).expect(200)).body.data.outgoing).toEqual([]);
    expect(await friendCount()).toBe(1);

    await send(ada, bob.id).expect(409);
    await deleteRequest(ada, id).expect(404);
  });

  it('allows participants to cancel/decline, but not outsiders', async () => {
    const id = (await send(ada, bob.id).expect(201)).body.data.id as string;

    await deleteRequest(carol, id).expect(404);
    await deleteRequest(ada, id).expect(204);
    await deleteRequest(bob, id).expect(404);
    expect(await friendCount()).toBe(0);

    const second = (await send(ada, bob.id).expect(201)).body.data.id as string;
    await deleteRequest(bob, second).expect(204);
    expect((await requests(ada).expect(200)).body.data.outgoing).toEqual([]);
  });

  it('lets either friend remove an accepted relation, never a pending one', async () => {
    const pendingId = (await send(ada, carol.id).expect(201)).body.data.id;
    await removeFriend(ada, carol.id).expect(404);
    expect(
      (await prisma.friendship.findUnique({ where: { id: pendingId } }))?.status,
    ).toBe('pending');

    const acceptedId = (await send(ada, bob.id).expect(201)).body.data.id;
    await accept(bob, acceptedId).expect(200);

    await removeFriend(carol, bob.id).expect(404);
    await removeFriend(bob, ada.id).expect(204);
    await removeFriend(ada, bob.id).expect(404);
    expect((await friends(ada).expect(200)).body.data).toEqual([]);

    // After unfriending, a new request is possible.
    await send(bob, ada.id).expect(201);
  });

  it('validates route UUIDs and preserves the request when they are malformed', async () => {
    const id = (await send(ada, bob.id).expect(201)).body.data.id;

    await accept(bob, 'nope').expect(400);
    await deleteRequest(ada, 'nope').expect(400);
    await removeFriend(ada, 'nope').expect(400);
    expect(await friendCount()).toBe(1);

    // PostgreSQL UUID comparison works regardless of string letter case.
    await accept(bob, id.toUpperCase()).expect(200);
    await removeFriend(ada, bob.id.toUpperCase()).expect(204);
    expect(await friendCount()).toBe(0);
  });
});
