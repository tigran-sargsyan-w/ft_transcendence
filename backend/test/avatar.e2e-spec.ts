import { randomUUID } from 'node:crypto';
import type { NestExpressApplication } from '@nestjs/platform-express';
import { Test } from '@nestjs/testing';
import { ThrottlerGuard } from '@nestjs/throttler';
import sharp from 'sharp';
import request from 'supertest';
import { AppModule } from '../src/app.module.js';
import { configureApp } from '../src/app.setup.js';
import { MAX_AVATAR_BYTES } from '../src/modules/users/avatar.service.js';
import { PrismaService } from '../src/shared/prisma/prisma.service.js';

describe('Avatars (e2e)', () => {
  let app: NestExpressApplication;
  let prisma: PrismaService;
  type Account = { id: string; cookie: string };
  let ada: Account;
  let bob: Account;

  const emails = ['avatar-e2e-ada@example.com', 'avatar-e2e-bob@example.com'];
  const password = 'password123';
  const http = () => request(app.getHttpServer());

  async function createUser(email: string, displayName: string): Promise<Account> {
    const registered = await http()
      .post('/api/v1/auth/register')
      .send({ email, password, displayName })
      .expect(201);

    const loggedIn = await http()
      .post('/api/v1/auth/login')
      .send({ email, password })
      .expect(200);

    return {
      id: registered.body.data.id as string,
      cookie: (loggedIn.get('Set-Cookie')?.[0] ?? '').split(';')[0],
    };
  }

  const upload = (who: Account, data: Buffer, filename = 'avatar.png') =>
    http().post('/api/v1/users/me/avatar')
      .set('Cookie', who.cookie)
      .attach('file', data, filename);

  const get = (who: Account, id: string) =>
    http().get(`/api/v1/users/${id}/avatar`)
      .set('Cookie', who.cookie);

  const profile = (who: Account, id: string) =>
    http().get(`/api/v1/users/${id}`)
      .set('Cookie', who.cookie);

  const remove = (who: Account) =>
    http().delete('/api/v1/users/me/avatar').set('Cookie', who.cookie);

  const png = (colour: string) =>
    sharp({ create: {
      width: 24, height: 16, channels: 3, background: colour,
    } }).png().toBuffer();

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
  });

  beforeEach(async () => {
    await prisma.user.deleteMany({ where: { email: { in: emails } } });
    ada = await createUser(emails[0], 'Ada');
    bob = await createUser(emails[1], 'Bob');
  });

  afterEach(async () => {
    await prisma.user.deleteMany({ where: { email: { in: emails } } });
  });

  afterAll(async () => {
    if (app) {
      await app.close();
    }
  });

  it('requires a valid session for upload, read and delete', async () => {
    const picture = await png('red');
    await http()
      .post('/api/v1/users/me/avatar')
      .attach('file', picture, 'avatar.png')
      .expect(401);
    await http().get(`/api/v1/users/${ada.id}/avatar`).expect(401);
    await http().delete('/api/v1/users/me/avatar').expect(401);
    expect(await prisma.userAvatar.count()).toBe(0);
  });

  it('starts with a null avatarUrl and returns 404 for no stored avatar', async () => {
    const before = await profile(bob, ada.id).expect(200);
    expect(before.body.data.avatarUrl).toBeNull();
    const missing = await get(bob, ada.id).expect(404);
    expect(missing.body.error.code).toBe('AVATAR_NOT_FOUND');
  });

  it('uploads a PNG, stores canonical WebP and exposes a safe URL', async () => {
    const picture = await png('red');
    const response = await upload(ada, picture).expect(201);
    const url = `/api/v1/users/${ada.id}/avatar`;

    expect(response.body.data.avatarUrl).toBe(url);
    expect(response.body.data).not.toHaveProperty('passwordHash');

    const stored = await prisma.userAvatar.findUniqueOrThrow({
      where: { userId: ada.id },
    });
    expect(stored.mimeType).toBe('image/webp');
    expect(Buffer.from(stored.data).toString('ascii', 0, 4)).toBe('RIFF');
    expect((await sharp(Buffer.from(stored.data)).metadata()).format).toBe('webp');

    const received = await get(bob, ada.id).expect(200);
    expect(received.headers['content-type']).toMatch(/^image\/webp/);
    expect(received.headers['cache-control']).toContain('no-store');
    expect(received.headers['x-content-type-options']).toBe('nosniff');
    expect(received.headers['content-security-policy']).toContain('sandbox');

    const publicProfile = await profile(bob, ada.id).expect(200);
    expect(publicProfile.body.data).toEqual({
      id: ada.id, displayName: 'Ada', avatarUrl: url,
    });
  });

  it('accepts JPEG/WebP and atomically replaces the image', async () => {
    const first = await sharp({ create: {
      width: 16, height: 16, channels: 3, background: 'blue',
    } }).jpeg().toBuffer();
    const second = await sharp({ create: {
      width: 16, height: 16, channels: 3, background: 'green',
    } }).webp().toBuffer();

    await upload(ada, first, 'first.jpg').expect(201);
    const before = await prisma.userAvatar.findUniqueOrThrow({
      where: { userId: ada.id },
    });

    await upload(ada, second, 'new.webp').expect(201);
    const after = await prisma.userAvatar.findUniqueOrThrow({
      where: { userId: ada.id },
    });

    expect(after.mimeType).toBe('image/webp');
    expect(Buffer.from(after.data).equals(Buffer.from(before.data))).toBe(false);
    expect(await prisma.userAvatar.count({ where: { userId: ada.id } })).toBe(1);
  });

  it('never lets a caller upload or delete another user\'s image', async () => {
    await upload(ada, await png('red')).expect(201);
    const before = await prisma.userAvatar.findUniqueOrThrow({
      where: { userId: ada.id },
    });

    await upload(bob, await png('blue')).expect(201);
    await remove(bob).expect(204);

    const after = await prisma.userAvatar.findUniqueOrThrow({
      where: { userId: ada.id },
    });
    expect(Buffer.from(after.data)).toEqual(Buffer.from(before.data));
    expect((await profile(bob, ada.id).expect(200)).body.data.avatarUrl).toBeTruthy();
    expect((await profile(ada, bob.id).expect(200)).body.data.avatarUrl).toBeNull();
  });

  it('rejects invalid/unsupported images and missing file fields', async () => {
    const invalid = await upload(ada, Buffer.from('not an image')).expect(400);
    expect(invalid.body.error.code).toBe('VALIDATION_ERROR');
    expect(invalid.body.error.details).toHaveProperty('file');

    const gif = await sharp({ create: {
      width: 8, height: 8, channels: 3, background: 'yellow',
    } }).gif().toBuffer();
    await upload(ada, gif, 'image.gif').expect(400);

    await http().post('/api/v1/users/me/avatar')
      .set('Cookie', ada.cookie)
      .expect(400);

    expect(await prisma.userAvatar.count({ where: { userId: ada.id } })).toBe(0);
  });

  it('rejects files exceeding 2 MiB without writing them', async () => {
    const response = await upload(
      ada, Buffer.alloc(MAX_AVATAR_BYTES + 1, 1), 'oversized.png',
    );
    expect(response.status).toBe(413);
    expect(await prisma.userAvatar.count({ where: { userId: ada.id } })).toBe(0);
  });

  it('rejects cross-origin multipart requests before parsing the upload', async () => {
    const picture = await png('red');
    const response = await http()
      .post('/api/v1/users/me/avatar')
      .set('Cookie', ada.cookie)
      .set('Origin', 'https://evil.invalid')
      .attach('file', picture, 'avatar.png')
      .expect(403);
    expect(response.body.error.code).toBe('INVALID_ORIGIN');

    await http()
      .post('/api/v1/users/me/avatar')
      .set('Cookie', ada.cookie)
      .set('Sec-Fetch-Site', 'cross-site')
      .attach('file', picture, 'avatar.png')
      .expect(403);
    expect(await prisma.userAvatar.count({ where: { userId: ada.id } })).toBe(0);
  });

  it('rejects malformed ids, extra fields and extra uploaded files', async () => {
    const picture = await png('red');
    await get(ada, 'not-a-uuid').expect(400);

    const extraField = await http().post('/api/v1/users/me/avatar')
      .set('Cookie', ada.cookie)
      .field('role', 'admin')
      .attach('file', picture, 'avatar.png');
    expect(extraField.status).toBe(400);

    const extraFile = await http().post('/api/v1/users/me/avatar')
      .set('Cookie', ada.cookie)
      .attach('file', picture, 'first.png')
      .attach('file', picture, 'second.png');
    expect(extraFile.status).toBe(400);

    expect(await prisma.userAvatar.count({ where: { userId: ada.id } })).toBe(0);
  });

  it('deletes the avatar idempotently and restores the default-avatar fallback', async () => {
    await upload(ada, await png('red')).expect(201);
    await remove(ada).expect(204);
    await remove(ada).expect(204);
    expect(await prisma.userAvatar.count({ where: { userId: ada.id } })).toBe(0);

    const me = await http().get('/api/v1/auth/me')
      .set('Cookie', ada.cookie).expect(200);
    expect(me.body.data.avatarUrl).toBeNull();
    expect((await profile(bob, ada.id).expect(200)).body.data.avatarUrl).toBeNull();
    await get(ada, ada.id).expect(404);
  });

  it('rejects an arbitrary non-existent image URL without disclosing accounts', async () => {
    const response = await get(ada, randomUUID()).expect(404);
    expect(response.body.error.code).toBe('AVATAR_NOT_FOUND');
  });
});
