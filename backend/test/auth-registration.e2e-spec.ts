import { type INestApplication } from '@nestjs/common';
import { Test } from '@nestjs/testing';
import * as argon2 from 'argon2';
import request from 'supertest';
import { AppModule } from '../src/app.module.js';
import { configureApp } from '../src/app.setup.js';
import { PrismaService } from '../src/shared/prisma/prisma.service.js';

describe('User registration (e2e)', () => {
  let app: INestApplication;
  let prisma: PrismaService;

  const email = 'registration-e2e@example.com';

  beforeAll(async () => {
    const moduleRef = await Test.createTestingModule({
      imports: [AppModule],
    }).compile();

    app = moduleRef.createNestApplication();
    configureApp(app);

    await app.init();

    prisma = app.get(PrismaService);
  });

  beforeEach(async () => {
    await prisma.user.deleteMany({
      where: { email },
    });
  });

  afterEach(async () => {
    await prisma.user.deleteMany({
      where: { email },
    });
  });

  afterAll(async () => {
    if (app) {
      await app.close();
    }
  });

  it('registers a user with a normalized email and hashed password', async () => {
    const response = await request(app.getHttpServer())
      .post('/api/v1/auth/register')
      .send({
        email: '  Registration-E2E@Example.COM ',
        password: 'password123',
      })
      .expect(201);

    expect(response.body.data.email).toBe(email);
    expect(response.body.data).toHaveProperty('id');
    expect(response.body.data).toHaveProperty('createdAt');
    expect(response.body.data).toHaveProperty('updatedAt');

    expect(response.body.data).not.toHaveProperty('password');
    expect(response.body.data).not.toHaveProperty('passwordHash');

    const storedUser = await prisma.user.findUnique({
      where: { email },
    });

    expect(storedUser).not.toBeNull();
    expect(storedUser?.passwordHash).not.toBe('password123');
    expect(storedUser?.passwordHash.startsWith('$argon2id$')).toBe(true);

    const passwordMatches = await argon2.verify(
      storedUser!.passwordHash,
      'password123',
    );

    expect(passwordMatches).toBe(true);
  });

  it('rejects invalid registration input', async () => {
    await request(app.getHttpServer())
      .post('/api/v1/auth/register')
      .send({
        email: 'not-an-email',
        password: '123',
      })
      .expect(400);
  });

  it('rejects missing required fields', async () => {
    await request(app.getHttpServer())
      .post('/api/v1/auth/register')
      .send({})
      .expect(400);
  });

  it('rejects unknown registration fields', async () => {
    await request(app.getHttpServer())
      .post('/api/v1/auth/register')
      .send({
        email,
        password: 'password123',
        isAdmin: true,
      })
      .expect(400);
  });

  it('rejects a duplicate normalized email', async () => {
    await request(app.getHttpServer())
      .post('/api/v1/auth/register')
      .send({
        email,
        password: 'password123',
      })
      .expect(201);

    const response = await request(app.getHttpServer())
      .post('/api/v1/auth/register')
      .send({
        email: ' REGISTRATION-E2E@EXAMPLE.COM ',
        password: 'another-password',
      })
      .expect(409);

    expect(response.body).toEqual({
      error: {
        code: 'EMAIL_ALREADY_EXISTS',
        message: 'Email is already registered',
      },
    });
  });
});
