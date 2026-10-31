import type { NestExpressApplication } from '@nestjs/platform-express';
import { Test } from '@nestjs/testing';
import request from 'supertest';
import { AppModule } from '../src/app.module.js';
import { configureApp } from '../src/app.setup.js';

// Separate spec: the attempt counter lives in memory, per app instance.
describe('Login rate limit (e2e)', () => {
  let app: NestExpressApplication;

  beforeAll(async () => {
    const moduleRef = await Test.createTestingModule({
      imports: [AppModule],
    }).compile();

    app = moduleRef.createNestApplication<NestExpressApplication>({
      bodyParser: false,
    });
    configureApp(app);

    await app.init();
  });

  afterAll(async () => {
    if (app) {
      await app.close();
    }
  });

  it('refuses the 11th login attempt within a minute', async () => {
    const attempt = () =>
      request(app.getHttpServer()).post('/api/v1/auth/login').send({
        email: 'throttle-e2e@example.com',
        password: 'wrong-password',
      });

    for (let i = 0; i < 10; i++) {
      await attempt().expect(401);
    }

    const response = await attempt().expect(429);

    expect(response.body.error.code).toBe('TOO_MANY_REQUESTS');
  });

  // Behind Nginx every request comes from Nginx itself; the client is the
  // address Nginx appends to X-Forwarded-For.
  const attemptFrom = (forwardedFor: string) =>
    request(app.getHttpServer())
      .post('/api/v1/auth/login')
      .set('X-Forwarded-For', forwardedFor)
      .send({
        email: 'throttle-proxy-e2e@example.com',
        password: 'wrong-password',
      });

  it('counts each client behind the proxy on its own', async () => {
    for (let i = 0; i < 10; i++) {
      await attemptFrom('203.0.113.1').expect(401);
    }

    await attemptFrom('203.0.113.1').expect(429);
    await attemptFrom('203.0.113.2').expect(401);
  });

  it('ignores the addresses a client writes before the one Nginx adds', async () => {
    for (let i = 0; i < 10; i++) {
      await attemptFrom(`198.51.100.${i}, 203.0.113.3`).expect(401);
    }

    await attemptFrom('198.51.100.99, 203.0.113.3').expect(429);
  });
});
