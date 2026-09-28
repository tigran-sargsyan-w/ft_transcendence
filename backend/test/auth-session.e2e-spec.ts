import type { NestExpressApplication } from '@nestjs/platform-express';
import { Test } from '@nestjs/testing';
import request from 'supertest';
import { AppModule } from '../src/app.module.js';
import { configureApp } from '../src/app.setup.js';
import { hashToken } from '../src/modules/auth/auth.service.js';
import { PrismaService } from '../src/shared/prisma/prisma.service.js';

describe('Sessions (e2e)', () => {
  let app: NestExpressApplication;
  let prisma: PrismaService;

  const email = 'session-e2e@example.com';
  const password = 'password123';

  const invalidCredentials = {
    error: {
      code: 'AUTH_INVALID_CREDENTIALS',
      message: 'Invalid credentials',
    },
  };

  // Returns the `sid` cookie value set by a successful login.
  async function login() {
    const response = await request(app.getHttpServer())
      .post('/api/v1/auth/login')
      .send({ email, password })
      .expect(200);

    const cookie = response.get('Set-Cookie')?.[0] ?? '';

    return cookie.split(';')[0].replace('sid=', '');
  }

  beforeAll(async () => {
    const moduleRef = await Test.createTestingModule({
      imports: [AppModule],
    }).compile();

    app = moduleRef.createNestApplication<NestExpressApplication>({
      bodyParser: false,
    });
    configureApp(app);

    await app.init();

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
    // Sessions go with the user (onDelete: Cascade).
    await prisma.user.deleteMany({ where: { email } });
  });

  afterAll(async () => {
    if (app) {
      await app.close();
    }
  });

  it('logs in, sets the session cookie and stores only its hash', async () => {
    const response = await request(app.getHttpServer())
      .post('/api/v1/auth/login')
      .send({ email: ' Session-E2E@Example.com ', password })
      .expect(200);

    expect(response.body.data.email).toBe(email);
    expect(response.body.data).toHaveProperty('id');
    expect(response.body.data).not.toHaveProperty('passwordHash');

    const cookie = response.get('Set-Cookie')?.[0] ?? '';

    expect(cookie).toMatch(/^sid=[\w-]+;/);
    expect(cookie).toContain('HttpOnly');
    expect(cookie).toContain('Secure');
    expect(cookie).toContain('SameSite=Lax');
    expect(cookie).toContain('Path=/');
    expect(cookie).toContain('Max-Age=');

    const token = cookie.split(';')[0].replace('sid=', '');
    const sessions = await prisma.session.findMany({
      where: { user: { email } },
    });

    expect(sessions).toHaveLength(1);
    expect(sessions[0].tokenHash).toBe(hashToken(token));
    expect(sessions[0].tokenHash).not.toBe(token);
  });

  it('answers a wrong password and an unknown email the same way', async () => {
    const wrongPassword = await request(app.getHttpServer())
      .post('/api/v1/auth/login')
      .send({ email, password: 'wrong-password' })
      .expect(401);

    const unknownEmail = await request(app.getHttpServer())
      .post('/api/v1/auth/login')
      .send({ email: 'nobody-e2e@example.com', password })
      .expect(401);

    expect(wrongPassword.body).toEqual(invalidCredentials);
    expect(unknownEmail.body).toEqual(invalidCredentials);
    expect(wrongPassword.get('Set-Cookie')).toBeUndefined();
  });

  it('creates a new session at each login', async () => {
    const first = await login();
    const second = await login();

    expect(first).not.toBe(second);
    expect(await prisma.session.count({ where: { user: { email } } })).toBe(2);
  });

  it('rejects unknown login fields', async () => {
    const response = await request(app.getHttpServer())
      .post('/api/v1/auth/login')
      .send({ email, password, remember: true })
      .expect(400);

    expect(response.body.error.code).toBe('VALIDATION_ERROR');
    expect(response.body.error.details).toEqual({
      remember: ['property remember should not exist'],
    });
  });

  it('ignores form-encoded bodies, as a cross-site form would send', async () => {
    const response = await request(app.getHttpServer())
      .post('/api/v1/auth/login')
      .type('form')
      .send({ email, password })
      .expect(400);

    expect(response.body.error.code).toBe('VALIDATION_ERROR');
    expect(response.get('Set-Cookie')).toBeUndefined();
  });
});
