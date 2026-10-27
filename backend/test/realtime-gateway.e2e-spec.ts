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

const unauthorized = { code: 'UNAUTHORIZED', message: 'Unauthorized' };
const forbidden = { code: 'FORBIDDEN', message: 'Forbidden' };

// Both transports of Socket.IO: the handshake is read from the first request.
const transports = ['websocket', 'polling'] as const;

describe('Realtime handshake (e2e)', () => {
  let app: NestExpressApplication;
  let prisma: PrismaService;
  let url: string;
  const sockets: Socket[] = [];

  const email = 'realtime-e2e@example.com';
  const password = 'password123';

  // Returns the `sid` cookie value set by a successful login.
  async function login() {
    const response = await request(app.getHttpServer())
      .post('/api/v1/auth/login')
      .send({ email, password })
      .expect(200);

    const cookie = response.get('Set-Cookie')?.[0] ?? '';

    return cookie.split(';')[0].replace('sid=', '');
  }

  // Opens a socket that does not reconnect and waits for its handshake: null
  // once connected, the `{ code, message }` of the refusal otherwise.
  function handshake(
    transport: (typeof transports)[number],
    headers: { cookie?: string; origin?: string } = {},
  ) {
    const socket = io(url, {
      transports: [transport],
      reconnection: false,
      forceNew: true,
      extraHeaders: headers,
    });

    sockets.push(socket);

    return new Promise<{ code: string; message: string } | null>((resolve) => {
      socket.once('connect', () => resolve(null));
      socket.once(
        'connect_error',
        (error: Error & { data?: { code: string; message: string } }) =>
          resolve(error.data ?? { code: 'NO_DATA', message: error.message }),
      );
    });
  }

  beforeAll(async () => {
    process.env.APP_ORIGIN = APP_ORIGIN;

    // The login rate limit has its own spec (auth-throttle).
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

    // A real port: the clients connect to it.
    await app.listen(0);

    const { port } = app.getHttpServer().address() as AddressInfo;

    url = `http://127.0.0.1:${port}`;
    prisma = app.get(PrismaService);
  });

  beforeEach(async () => {
    await prisma.user.deleteMany({ where: { email } });

    await request(app.getHttpServer())
      .post('/api/v1/auth/register')
      .send({ email, password })
      .expect(201);
  });

  afterEach(async () => {
    sockets.splice(0).forEach((socket) => socket.close());

    // Sessions go with the user (onDelete: Cascade).
    await prisma.user.deleteMany({ where: { email } });
  });

  afterAll(async () => {
    delete process.env.APP_ORIGIN;

    if (app) {
      await app.close();
    }
  });

  it.each(transports)(
    'refuses a handshake without a session (%s)',
    async (transport) => {
      expect(await handshake(transport)).toEqual(unauthorized);
    },
  );

  it.each(transports)(
    'refuses a made-up session cookie (%s)',
    async (transport) => {
      expect(await handshake(transport, { cookie: 'sid=made-up' })).toEqual(
        unauthorized,
      );
    },
  );

  it.each(transports)(
    'accepts a handshake with a session (%s)',
    async (transport) => {
      const token = await login();

      expect(await handshake(transport, { cookie: `sid=${token}` })).toBeNull();
    },
  );

  it('refuses an expired session', async () => {
    const token = await login();

    await prisma.session.update({
      where: { tokenHash: hashToken(token) },
      data: { expiresAt: new Date(Date.now() - 1000) },
    });

    expect(await handshake('websocket', { cookie: `sid=${token}` })).toEqual(
      unauthorized,
    );
  });

  it('refuses an Origin that is not the application, even with a session', async () => {
    const token = await login();

    expect(
      await handshake('websocket', {
        cookie: `sid=${token}`,
        origin: 'https://evil.example',
      }),
    ).toEqual(forbidden);
  });

  it('accepts the Origin of the application', async () => {
    const token = await login();

    expect(
      await handshake('websocket', {
        cookie: `sid=${token}`,
        origin: APP_ORIGIN,
      }),
    ).toBeNull();
  });

  it('refuses any Origin while APP_ORIGIN is not set', async () => {
    const token = await login();

    delete process.env.APP_ORIGIN;

    try {
      expect(
        await handshake('websocket', {
          cookie: `sid=${token}`,
          origin: APP_ORIGIN,
        }),
      ).toEqual(forbidden);
    } finally {
      process.env.APP_ORIGIN = APP_ORIGIN;
    }
  });
});
