import type { NestExpressApplication } from '@nestjs/platform-express';
import { Test } from '@nestjs/testing';
import request from 'supertest';
import { AppModule } from '../src/app.module.js';
import { configureApp } from '../src/app.setup.js';
import { PrismaService } from '../src/shared/prisma/prisma.service.js';

const email = 'analysis-throttle-e2e@example.com';
const password = 'password123';

// Separate spec: the counter lives in memory, per app instance, and
// analysis.e2e-spec.ts turns the guard off.
describe('Analyses rate limit (e2e)', () => {
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

  // An empty list of analyses is refused with a 400 as soon as the guards let
  // it through, so neither the topology nor the engine is ever involved.
  const attemptFrom = (forwardedFor: string) =>
    request(app.getHttpServer())
      .post('/api/v1/environments/demo/analyses')
      .set('Cookie', cookie)
      .set('X-Forwarded-For', forwardedFor)
      .send({ analyses: [] });

  it('refuses the 31st analysis within a minute, for that client only', async () => {
    for (let i = 0; i < 30; i++) {
      await attemptFrom('203.0.113.1').expect(400);
    }

    const response = await attemptFrom('203.0.113.1').expect(429);

    expect(response.body.error.code).toBe('TOO_MANY_REQUESTS');
    await attemptFrom('203.0.113.2').expect(400);
  });
});
