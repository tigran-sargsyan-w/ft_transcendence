import type { NestExpressApplication } from '@nestjs/platform-express';
import { Test } from '@nestjs/testing';
import { randomUUID } from 'node:crypto';
import request from 'supertest';
import { AppModule } from '../src/app.module.js';
import { configureApp } from '../src/app.setup.js';
import { PrismaService } from '../src/shared/prisma/prisma.service.js';

const email = 'incidents-throttle-e2e@example.com';
const password = 'password123';

// Separate spec: the counter lives in memory, per app instance, and
// incidents.e2e-spec.ts turns the guard off.
describe('Incidents rate limit (e2e)', () => {
  let app: NestExpressApplication;
  let prisma: PrismaService;
  let cookie: string;

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
  });

  // An empty body is refused with a 400 once the guards let it through, so
  // nothing is written and no topology is needed.
  const open = (forwardedFor: string) =>
    request(app.getHttpServer())
      .post('/api/v1/incidents')
      .set('Cookie', cookie)
      .set('X-Forwarded-For', forwardedFor)
      .send({});

  const note = (forwardedFor: string) =>
    request(app.getHttpServer())
      .post(`/api/v1/incidents/${randomUUID()}/notes`)
      .set('Cookie', cookie)
      .set('X-Forwarded-For', forwardedFor)
      .send({});

  it('refuses the 31st incident within a minute, for that client only', async () => {
    for (let i = 0; i < 30; i++) {
      await open('203.0.113.11').expect(400);
    }

    const response = await open('203.0.113.11').expect(429);

    expect(response.body.error.code).toBe('TOO_MANY_REQUESTS');
    await open('203.0.113.12').expect(400);
  });

  it('refuses the 31st note within a minute, for that client only', async () => {
    for (let i = 0; i < 30; i++) {
      await note('203.0.113.21').expect(400);
    }

    await note('203.0.113.21').expect(429);
    await note('203.0.113.22').expect(400);
  });

  // Same client, blocked on the incidents: the notes and the login have their
  // own counters.
  it('counts the incidents apart from the notes and from the login', async () => {
    for (let i = 0; i < 30; i++) {
      await open('203.0.113.31').expect(400);
    }

    await open('203.0.113.31').expect(429);
    await note('203.0.113.31').expect(400);
    await request(app.getHttpServer())
      .post('/api/v1/auth/login')
      .set('X-Forwarded-For', '203.0.113.31')
      .send({ email, password })
      .expect(200);
  });

  it('does not limit reading', async () => {
    for (let i = 0; i < 40; i++) {
      await request(app.getHttpServer())
        .get('/api/v1/incidents')
        .set('Cookie', cookie)
        .set('X-Forwarded-For', '203.0.113.41')
        .expect(200);
    }
  });
});
