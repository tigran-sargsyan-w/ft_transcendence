import { Logger } from '@nestjs/common';
import type { NestExpressApplication } from '@nestjs/platform-express';
import { Test } from '@nestjs/testing';
import request from 'supertest';
import { AppModule } from '../src/app.module.js';
import { configureApp } from '../src/app.setup.js';
import { AuthService } from '../src/modules/auth/auth.service.js';
import { PrismaService } from '../src/shared/prisma/prisma.service.js';

describe('API response format (e2e)', () => {
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

  it('wraps a successful response in data', async () => {
    const response = await request(app.getHttpServer())
      .get('/api/v1/health')
      .expect(200);

    expect(response.body).toEqual({ data: { status: 'ok', database: 'ok' } });
  });

  it('names the failing dependency in the health error details', async () => {
    vi.spyOn(app.get(PrismaService), '$queryRaw').mockRejectedValueOnce(
      new Error('connection refused'),
    );

    const response = await request(app.getHttpServer())
      .get('/api/v1/health')
      .expect(503);

    expect(response.body).toEqual({
      error: {
        code: 'SERVICE_UNAVAILABLE',
        message: 'Service unavailable',
        details: { database: 'unavailable' },
      },
    });
  });

  it('reports the migrations the database has not applied', async () => {
    // The first query (SELECT 1) answers, the second finds nothing applied.
    vi.spyOn(app.get(PrismaService), '$queryRaw')
      .mockResolvedValueOnce([{ '?column?': 1 }])
      .mockResolvedValueOnce([]);

    const response = await request(app.getHttpServer())
      .get('/api/v1/health')
      .expect(503);

    expect(response.body).toEqual({
      error: {
        code: 'SERVICE_UNAVAILABLE',
        message: 'Service unavailable',
        details: { migrations: 'pending' },
      },
    });
  });

  it('answers 503 when it cannot read the migrations of the repository', async () => {
    // The database is not mocked: the folder of the migrations is what is
    // missing (it is read from the working directory).
    const cwd = vi.spyOn(process, 'cwd').mockReturnValue('/nowhere');

    try {
      const response = await request(app.getHttpServer())
        .get('/api/v1/health')
        .expect(503);

      expect(response.body).toEqual({
        error: {
          code: 'SERVICE_UNAVAILABLE',
          message: 'Service unavailable',
          details: { migrations: 'unavailable' },
        },
      });
    } finally {
      cwd.mockRestore();
    }
  });

  it('formats an unknown route as a 404 error', async () => {
    const response = await request(app.getHttpServer())
      .get('/api/v1/nope')
      .expect(404);

    expect(response.body).toEqual({
      error: { code: 'NOT_FOUND', message: 'Cannot GET /api/v1/nope' },
    });
  });

  it('formats malformed JSON as a 400 error', async () => {
    const response = await request(app.getHttpServer())
      .post('/api/v1/auth/register')
      .set('Content-Type', 'application/json')
      .send('{bad')
      .expect(400);

    expect(response.body.error.code).toBe('BAD_REQUEST');
  });

  it('keeps the status of body-parser errors, such as a 413', async () => {
    const response = await request(app.getHttpServer())
      .post('/api/v1/auth/register')
      .send({ email: 'big-e2e@example.com', password: 'x'.repeat(200_000) })
      .expect(413);

    expect(response.body.error.code).toBe('PAYLOAD_TOO_LARGE');
  });

  it('hides unexpected errors behind a generic 500 and logs them', async () => {
    const failure = new Error('secret db detail');
    const logError = vi
      .spyOn(Logger.prototype, 'error')
      .mockImplementation(() => {});
    vi.spyOn(app.get(AuthService), 'register').mockRejectedValueOnce(failure);

    const response = await request(app.getHttpServer())
      .post('/api/v1/auth/register')
      .send({ email: 'boom-e2e@example.com', password: 'password123' })
      .expect(500);

    expect(response.body).toEqual({
      error: { code: 'INTERNAL_ERROR', message: 'Internal server error' },
    });
    expect(JSON.stringify(response.body)).not.toContain('secret');
    expect(logError).toHaveBeenCalledWith(failure);

    logError.mockRestore();
  });
});
