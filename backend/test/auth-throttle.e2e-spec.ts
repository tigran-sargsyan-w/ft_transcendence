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
});
