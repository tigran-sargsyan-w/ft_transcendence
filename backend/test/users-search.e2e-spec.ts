import type { NestExpressApplication } from '@nestjs/platform-express';
import { Test } from '@nestjs/testing';
import { ThrottlerGuard } from '@nestjs/throttler';
import request from 'supertest';
import { AppModule } from '../src/app.module.js';
import { configureApp } from '../src/app.setup.js';
import { PrismaService } from '../src/shared/prisma/prisma.service.js';

describe('User search (e2e)', () => {
  let app: NestExpressApplication;
  let prisma: PrismaService;

  const password = 'password123';
  const accounts = [
    { email: 'search-e2e-ada@example.com', displayName: 'SearchCaseAda' },
    { email: 'search-e2e-bob@example.com', displayName: 'SearchCaseBob' },
    { email: 'search-e2e-bobby@example.com', displayName: 'SearchCaseBobby' },
    {
      email: 'search-e2e-percent@example.com',
      displayName: 'SearchPct100% Real',
    }
    {
      email: 'search-e2e-under@example.com',
      displayName: 'SearchUnder_a_bench',
    }
    {
      email: 'search-e2e-slash@example.com',
      displayName: 'SearchSlashPath\\Name',
    }
    {
      email: 'search-e2e-secretmarker@example.com',
      displayName: 'SearchCarol',
    }
  ];
  type Account = { id: string; cookie: string };
  let ada: Account;
  let bob: Account;

  const http = () => request(app.getHttpServer());

  async function register(email: string, displayName: string): Promise<Account> {
    const created = await http()
      .post('/api/v1/auth/register')
      .send({ email, displayName, password })
      .expect(201);
    const login = await http()
      .post('/api/v1/auth/login')
      .send({ email, password })
      .expect(200);

    return {
      id: created.body.data.id as string,
      cookie: (login.get('Set-Cookie')?.[0] ?? '').split(';')[0],
    };
  }

  const search = (who: Account, query: Record<string, unknown>) =>
    http().get('/api/v1/users').set('Cookie', who.cookie).query(query);

  beforeAll(async () => {
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
    await prisma.user.deleteMany({
      where: { email: { in: accounts.map((entry) => entry.email) } },
    });

    const created = [];
    for (const account of accounts) {
      created.push(await register(account.email, account.displayName));
    }
    ada = created[0];
    bob = created[1];
  });

  afterAll(async () => {
    if (app) {
      await prisma.user.deleteMany({
        where: { email: { in: accounts.map((entry) => entry.email) } },
      });
      await app.close();
    }
  });

  it('requires an authenticated session', async () => {
    const response = await http()
      .get('/api/v1/users')
      .query({ search: 'SearchCase' })
      .expect(401);

    expect(response.body.error.code).toBe('UNAUTHORIZED');
  });

  it('searches by public display name case-insensitively in stable order', async () => {
    const response = await search(ada, { search: 'SEARCHCASEBO' }).expect(200);

    expect(
      response.body.data.map((user: { displayName: string }) => user.displayName),
    ).toEqual(['SearchCaseBob', 'SearchCaseBobby']);
    expect(response.body.data[0]).toEqual({
      id: bob.id,
      displayName: 'SearchCaseBob',
      avatarUrl: null,
    });
    for (const user of response.body.data) {
      expect(Object.keys(user).sort()).toEqual([
        'avatarUrl',
        'displayName',
        'id',
      ]);
    }
  });

  it('trims search text and excludes the signed-in user', async () => {
    const results = await search(ada, { search: '  SearchCase  ' }).expect(200);
    expect(
      results.body.data.map((user: { id: string }) => user.id),
    ).not.toContain(ada.id);
    expect(results.body.data).toHaveLength(2);

    const ownName = await search(ada, { search: 'SearchCaseAda' }).expect(200);
    expect(ownName.body.data).toEqual([]);
  });

  it('enforces the requested result limit', async () => {
    const result = await search(ada, {
      search: 'SearchCaseBo',
      limit: '1',
    }).expect(200);

    expect(result.body.data).toEqual([
      { id: bob.id, displayName: 'SearchCaseBob', avatarUrl: null },
    ]);
  });

  it('does not search private email fields or return private data', async () => {
    const result = await search(ada, { search: 'secretmarker' }).expect(200);
    expect(result.body.data).toEqual([]);

    const all = await search(ada, { search: 'Search' }).expect(200);
    expect(JSON.stringify(all.body)).not.toContain('@example.com');
    expect(JSON.stringify(all.body)).not.toContain('passwordHash');
    expect(JSON.stringify(all.body)).not.toContain('isOnline');
  });

  it('treats percent, underscore and backslash as literal characters', async () => {
    const percent = await search(ada, { search: '100%' }).expect(200);
    expect(
      percent.body.data.map((user: { displayName: string }) => user.displayName),
    ).toEqual(['SearchPct100% Real']);

    const underscore = await search(ada, { search: 'Under_' }).expect(200);
    expect(
      underscore.body.data.map((user: { displayName: string }) => user.displayName),
    ).toEqual(['SearchUnder_a_bench']);

    const slash = await search(ada, { search: 'Path\\' }).expect(200);
    expect(
      slash.body.data.map((user: { displayName: string }) => user.displayName),
    ).toEqual(['SearchSlashPath\\Name']);
  });

  it('rejects invalid searches and unknown query keys', async () => {
    for (const query of [
      {},
      { search: '' },
      { search: 'a' },
      { search: '  ' },
      { search: 'x'.repeat(65) },
      { search: 'bo\0b' },
      { search: ['Bob', 'Bobby'] },
      { search: 'Bob', extra: true },
    ]) {
      const response = await search(ada, query).expect(400);
      expect(response.body.error.code).toBe('VALIDATION_ERROR');
    }
  });

  it('rejects invalid limits rather than silently coercing values', async () => {
    for (const limit of [
      '0', '-1', '21', '1.5', '1e1', '0x10', '01', 'oops', '',
    ]) {
      const response = await search(ada, {
        search: 'Search',
        limit,
      }).expect(400);

      expect(response.body.error.code).toBe('VALIDATION_ERROR');
      expect(response.body.error.details).toHaveProperty('limit');
    }
    const array = await search(ada, {
      search: 'Search',
      limit: ['1', '2'],
    }).expect(400);
    expect(array.body.error.code).toBe('VALIDATION_ERROR');
  });

  it('preserves the existing public profile route', async () => {
    const response = await http()
      .get('/api/v1/users/' + bob.id)
      .set('Cookie', ada.cookie)
      .expect(200);

    expect(response.body.data).toEqual({
      id: bob.id,
      displayName: 'SearchCaseBob',
      avatarUrl: null,
    });
  });
});
