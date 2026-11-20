import type { NestExpressApplication } from '@nestjs/platform-express';
import { Test } from '@nestjs/testing';
import { ThrottlerGuard } from '@nestjs/throttler';
import type { AddressInfo } from 'node:net';
import { io, type Socket } from 'socket.io-client';
import request from 'supertest';
import { AppModule } from '../src/app.module.js';
import { configureApp } from '../src/app.setup.js';
import { hashToken } from '../src/modules/auth/auth.service.js';
import { PrismaService } from '../src/shared/prisma/prisma.service.js';

const APP_ORIGIN = 'https://transcendence.test:8443';
const password = 'password123';
const emails = [
  'presence-e2e-ada@example.com',
  'presence-e2e-bob@example.com',
  'presence-e2e-carol@example.com',
];

type Account = { id: string; cookie: string };
type Change = {
  event: string;
  payload: { userId: string; isOnline: boolean };
  timestamp: string;
};

describe('Friend-only presence (e2e)', () => {
  let app: NestExpressApplication;
  let prisma: PrismaService;
  let url: string;
  let ada: Account;
  let bob: Account;
  let carol: Account;
  const sockets: Socket[] = [];

  const http = () => request(app.getHttpServer());

  async function login(email: string) {
    const response = await http()
      .post('/api/v1/auth/login')
      .send({ email, password })
      .expect(200);
    return (response.get('Set-Cookie')?.[0] ?? '').split(';')[0];
  }

  async function register(email: string, name: string): Promise<Account> {
    const response = await http()
      .post('/api/v1/auth/register')
      .send({ email, displayName: name, password })
      .expect(201);
    return { id: response.body.data.id as string, cookie: await login(email) };
  }

  async function befriend(first: Account, second: Account) {
    const response = await http()
      .post('/api/v1/users/me/friend-requests')
      .set('Cookie', first.cookie)
      .send({ targetUserId: second.id })
      .expect(201);

    await http()
      .post('/api/v1/users/me/friend-requests/' + response.body.data.id + '/accept')
      .set('Cookie', second.cookie)
      .expect(200);
  }

  const friends = (account: Account) =>
    http().get('/api/v1/users/me/friends').set('Cookie', account.cookie);

  async function connect(cookie: string) {
    const socket = io(url, {
      transports: ['websocket'],
      forceNew: true,
      reconnection: false,
      autoConnect: false,
      extraHeaders: { cookie },
    });
    sockets.push(socket);

    await new Promise<void>((resolve, reject) => {
      socket.once('connect', resolve);
      socket.once('connect_error', reject);
      socket.connect();
    });

    return socket;
  }

  // Attach before triggering the transition to avoid missing an event.
  function expectChange(socket: Socket, userId: string, isOnline: boolean) {
    return new Promise<Change>((resolve, reject) => {
      const timeout = setTimeout(() => {
        socket.off('presence:changed', handler);
        reject(new Error('Timed out waiting for friend presence event'));
      }, 6_500);

      const handler = (message: Change) => {
        if (
          message.payload.userId !== userId ||
          message.payload.isOnline !== isOnline
        ) {
          return;
        }
        clearTimeout(timeout);
        socket.off('presence:changed', handler);
        resolve(message);
      };

      socket.on('presence:changed', handler);
    });
  }

  beforeAll(async () => {
    process.env.APP_ORIGIN = APP_ORIGIN;
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
    await app.listen(0);

    const { port } = app.getHttpServer().address() as AddressInfo;
    url = 'http://127.0.0.1:' + port;
    prisma = app.get(PrismaService);
  });

  beforeEach(async () => {
    await prisma.user.deleteMany({ where: { email: { in: emails } } });
    ada = await register(emails[0], 'Ada');
    bob = await register(emails[1], 'Bob');
    carol = await register(emails[2], 'Carol');
  });

  afterEach(async () => {
    sockets.splice(0).forEach((socket) => socket.disconnect());
    await prisma.user.deleteMany({ where: { email: { in: emails } } });
  });

  afterAll(async () => {
    delete process.env.APP_ORIGIN;
    if (app) {
      await app.close();
    }
  });

  it('returns offline snapshots and never exposes presence in public profiles', async () => {
    await befriend(ada, bob);
    const result = (await friends(ada).expect(200)).body.data;

    expect(result).toMatchObject([
      { user: { id: bob.id, displayName: 'Bob' }, isOnline: false },
    ]);
    expect(result[0].user).not.toHaveProperty('isOnline');
    expect(result[0].user).not.toHaveProperty('email');

    const publicProfile = await http()
      .get('/api/v1/users/' + bob.id)
      .set('Cookie', carol.cookie)
      .expect(200);
    expect(publicProfile.body.data).not.toHaveProperty('isOnline');
  });

  it('announces first connect only to accepted friends, then updates snapshots', async () => {
    await befriend(ada, bob);
    // A pending request is not permission to see presence.
    await http()
      .post('/api/v1/users/me/friend-requests')
      .set('Cookie', carol.cookie)
      .send({ targetUserId: bob.id })
      .expect(201);

    const adaSocket = await connect(ada.cookie);
    const carolSocket = await connect(carol.cookie);
    const outsiderEvents: Change[] = [];
    carolSocket.on('presence:changed', (change: Change) =>
      outsiderEvents.push(change),
    );

    const update = expectChange(adaSocket, bob.id, true);
    await connect(bob.cookie);
    expect(await update).toMatchObject({
      event: 'presence:changed',
      payload: { userId: bob.id, isOnline: true },
      timestamp: expect.any(String),
    });

    expect((await friends(ada).expect(200)).body.data[0].isOnline).toBe(true);
    expect((await friends(bob).expect(200)).body.data[0].isOnline).toBe(true);
    expect((await friends(carol).expect(200)).body.data).toEqual([]);
    expect(outsiderEvents).toEqual([]);
  });

  it('keeps a user online across tabs and closes only the logged-out session', async () => {
    await befriend(ada, bob);
    const observer = await connect(ada.cookie);
    const bobFirst = await connect(bob.cookie);
    const secondCookie = await login(emails[1]);
    const bobSecond = await connect(secondCookie);

    bobFirst.disconnect();
    expect((await friends(ada).expect(200)).body.data[0].isOnline).toBe(true);

    const gone = expectChange(observer, bob.id, false);
    const disconnected = new Promise<void>((resolve) =>
      bobSecond.once('disconnect', () => resolve()),
    );

    await http()
      .post('/api/v1/auth/logout')
      .set('Cookie', secondCookie)
      .expect(204);

    await disconnected;
    expect((await gone).payload).toEqual({ userId: bob.id, isOnline: false });
    expect((await friends(ada).expect(200)).body.data[0].isOnline).toBe(false);
    expect(bobSecond.connected).toBe(false);
  });

  it('reports offline after the last connection grace, but not before', async () => {
    await befriend(ada, bob);
    const observer = await connect(ada.cookie);
    const subject = await connect(bob.cookie);
    const gone = expectChange(observer, bob.id, false);

    subject.disconnect();
    expect((await friends(ada).expect(200)).body.data[0].isOnline).toBe(true);

    expect((await gone).payload).toEqual({ userId: bob.id, isOnline: false });
    expect((await friends(ada).expect(200)).body.data[0].isOnline).toBe(false);
  }, 12_000);

  it('immediately reports offline on logout without leaking to removed friends', async () => {
    await befriend(ada, bob);
    const observer = await connect(ada.cookie);
    await connect(bob.cookie);

    await http()
      .delete('/api/v1/users/me/friends/' + bob.id)
      .set('Cookie', ada.cookie)
      .expect(204);

    const seen: Change[] = [];
    observer.on('presence:changed', (change: Change) => seen.push(change));

    await http()
      .post('/api/v1/auth/logout')
      .set('Cookie', bob.cookie)
      .expect(204);

    expect((await friends(ada).expect(200)).body.data).toEqual([]);
    // The notifications are serialized; after the HTTP call completes there
    // may still be a queued broadcast, which must never include Ada.
    await new Promise((resolve) => setTimeout(resolve, 150));
    expect(seen.filter((change) => change.payload.userId === bob.id)).toEqual([]);
  });

  it('disconnects an expired socket and emits offline immediately', async () => {
    await befriend(ada, bob);
    const observer = await connect(ada.cookie);
    const second = await login(emails[1]);
    const sid = second.replace('sid=', '');

    await prisma.session.update({
      where: { tokenHash: hashToken(sid) },
      data: { expiresAt: new Date(Date.now() + 1_500) },
    });

    const socket = await connect(second);
    const gone = expectChange(observer, bob.id, false);

    expect((await gone).payload).toEqual({ userId: bob.id, isOnline: false });
    expect(socket.connected).toBe(false);
    expect((await friends(ada).expect(200)).body.data[0].isOnline).toBe(false);
  });
});
