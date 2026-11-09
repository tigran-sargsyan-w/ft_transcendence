import type { NestExpressApplication } from '@nestjs/platform-express';
import { Test } from '@nestjs/testing';
import { ThrottlerGuard } from '@nestjs/throttler';
import request from 'supertest';
import { AppModule } from '../src/app.module.js';
import { configureApp } from '../src/app.setup.js';
import { PrismaService } from '../src/shared/prisma/prisma.service.js';

describe('Users (e2e)', () => {
  let app: NestExpressApplication;
  let prisma: PrismaService;

  const password = 'password123';
  const emails = ['users-e2e-a@example.com', 'users-e2e-b@example.com'];

  // Registers a user and logs in: returns its id and its session cookie.
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

  beforeAll(async () => {
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

    await app.init();

    prisma = app.get(PrismaService);
  });

  beforeEach(async () => {
    await prisma.user.deleteMany({ where: { email: { in: emails } } });
  });

  afterEach(async () => {
    await prisma.user.deleteMany({ where: { email: { in: emails } } });
  });

  afterAll(async () => {
    if (app) {
      await app.close();
    }
  });

  it('shows another user only the id, the display name and the avatar', async () => {
    const ada = await createUser(emails[0], 'Ada');
    const bob = await createUser(emails[1], 'Bob');

    const response = await request(app.getHttpServer())
      .get(`/api/v1/users/${ada.id}`)
      .set('Cookie', bob.cookie)
      .expect(200);

    expect(response.body.data).toEqual({
      id: ada.id,
      displayName: 'Ada',
      avatarUrl: null,
    });
  });

  it('refuses a profile without a session', async () => {
    const ada = await createUser(emails[0], 'Ada');

    const response = await request(app.getHttpServer())
      .get(`/api/v1/users/${ada.id}`)
      .expect(401);

    expect(response.body.error.code).toBe('UNAUTHORIZED');
  });

  it('answers 400 to an id that is not a UUID', async () => {
    const ada = await createUser(emails[0], 'Ada');

    const response = await request(app.getHttpServer())
      .get('/api/v1/users/not-a-uuid')
      .set('Cookie', ada.cookie)
      .expect(400);

    expect(response.body.error.code).toBe('BAD_REQUEST');
  });

  it('answers 404 to an unknown user', async () => {
    const ada = await createUser(emails[0], 'Ada');

    const response = await request(app.getHttpServer())
      .get('/api/v1/users/00000000-0000-4000-8000-000000000000')
      .set('Cookie', ada.cookie)
      .expect(404);

    expect(response.body).toEqual({
      error: { code: 'USER_NOT_FOUND', message: 'User not found' },
    });
  });

  it('changes the display name of the logged-in user only', async () => {
    const ada = await createUser(emails[0], 'Ada');
    const bob = await createUser(emails[1], 'Bob');

    const response = await request(app.getHttpServer())
      .patch('/api/v1/users/me')
      .set('Cookie', ada.cookie)
      .send({ displayName: '  Ada <i>L.</i> ' })
      .expect(200);

    const me = await request(app.getHttpServer())
      .get('/api/v1/auth/me')
      .set('Cookie', ada.cookie)
      .expect(200);

    expect(response.body.data.displayName).toBe('Ada <i>L.</i>');
    expect(response.body.data).toEqual(me.body.data);
    expect(response.body.data).not.toHaveProperty('passwordHash');

    const bobAfter = await prisma.user.findUniqueOrThrow({
      where: { id: bob.id },
    });

    expect(bobAfter.displayName).toBe('Bob');
  });

  it('refuses to change the profile without a session', async () => {
    const ada = await createUser(emails[0], 'Ada');

    await request(app.getHttpServer())
      .patch('/api/v1/users/me')
      .send({ displayName: 'Hacked' })
      .expect(401);

    const adaAfter = await prisma.user.findUniqueOrThrow({
      where: { id: ada.id },
    });

    expect(adaAfter.displayName).toBe('Ada');
  });

  it.each([
    ['missing', {}],
    ['empty', { displayName: '' }],
    ['only spaces', { displayName: '   ' }],
    ['too long', { displayName: 'a'.repeat(65) }],
    ['not a string', { displayName: 42 }],
  ])('rejects a display name that is %s', async (_label, body) => {
    const ada = await createUser(emails[0], 'Ada');

    const response = await request(app.getHttpServer())
      .patch('/api/v1/users/me')
      .set('Cookie', ada.cookie)
      .send(body)
      .expect(400);

    expect(response.body.error.code).toBe('VALIDATION_ERROR');
    expect(response.body.error.details).toHaveProperty('displayName');
  });

  it('rejects fields other than the display name', async () => {
    const ada = await createUser(emails[0], 'Ada');

    const response = await request(app.getHttpServer())
      .patch('/api/v1/users/me')
      .set('Cookie', ada.cookie)
      .send({ displayName: 'Ada', email: 'other@example.com' })
      .expect(400);

    expect(response.body.error.details).toEqual({
      email: ['property email should not exist'],
    });
  });
});
