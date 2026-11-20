import {
  BadRequestException,
  NotFoundException,
  PayloadTooLargeException,
} from '@nestjs/common';
import sharp from 'sharp';
import type { PrismaService } from '../../src/shared/prisma/prisma.service.js';
import {
  AvatarService,
  MAX_AVATAR_BYTES,
} from '../../src/modules/users/avatar.service.js';
import { AvatarOriginGuard } from '../../src/modules/users/avatar-origin.guard.js';

const ADA = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
const PROFILE = {
  id: ADA,
  displayName: 'Ada',
  email: 'ada@example.com',
  avatarUrl: `/api/v1/users/${ADA}/avatar`,
};

function subject() {
  const prisma = {
    userAvatar: {
      upsert: vi.fn().mockResolvedValue({ userId: ADA }),
      findUnique: vi.fn(),
      deleteMany: vi.fn().mockResolvedValue({ count: 1 }),
    },
    user: {
      update: vi.fn().mockResolvedValue(PROFILE),
    },
    $transaction: vi.fn(async (operations: Promise<unknown>[]) =>
      Promise.all(operations)),
  };

  return {
    prisma,
    avatars: new AvatarService(prisma as unknown as PrismaService),
  };
}

async function image(format: 'jpeg' | 'png' | 'webp', width = 32, height = 24) {
  return sharp({
    create: {
      width,
      height,
      channels: 4,
      background: { r: 50, g: 100, b: 150, alpha: 1 },
    },
  })[format]().toBuffer();
}

describe('AvatarService', () => {
  it.each(['jpeg', 'png', 'webp'] as const)(
    'normalizes %s uploads into canonical WebP bytes',
    async (format) => {
      const { prisma, avatars } = subject();
      const original = await image(format);

      const returned = await avatars.upload(ADA, {
        buffer: original,
        size: original.length,
      });

      expect(returned).toEqual(PROFILE);
      expect(prisma.$transaction).toHaveBeenCalledOnce();

      const payload = prisma.userAvatar.upsert.mock.calls[0][0].create;
      expect(payload.userId).toBe(ADA);
      expect(payload.mimeType).toBe('image/webp');
      expect(Buffer.from(payload.data).toString('ascii', 0, 4)).toBe('RIFF');
      expect(Buffer.from(payload.data).toString('ascii', 8, 12)).toBe('WEBP');
      expect(payload.data.length).toBeLessThanOrEqual(MAX_AVATAR_BYTES);
      expect((await sharp(Buffer.from(payload.data)).metadata()).format).toBe('webp');
      expect(prisma.user.update).toHaveBeenCalledWith(
        expect.objectContaining({
          data: { avatarUrl: `/api/v1/users/${ADA}/avatar` },
        }),
      );
    },
  );

  it('resizes large dimensions to 1024px and strips metadata', async () => {
    const { prisma, avatars } = subject();
    const original = await image('jpeg', 1700, 900);

    await avatars.upload(ADA, { buffer: original, size: original.length });
    const result = Buffer.from(prisma.userAvatar.upsert.mock.calls[0][0].create.data);
    const metadata = await sharp(result).metadata();

    expect(metadata.width).toBe(1024);
    expect(metadata.height).toBeLessThanOrEqual(1024);
    expect(metadata.exif).toBeUndefined();
    expect(metadata.icc).toBeUndefined();
  });

  it('rejects missing, malformed and unsupported image formats before storage', async () => {
    const { prisma, avatars } = subject();
    const gif = await sharp({
      create: { width: 2, height: 2, channels: 3, background: 'red' },
    }).gif().toBuffer();

    for (const file of [
      undefined,
      { buffer: Buffer.from('not an image'), size: 12 },
      { buffer: gif, size: gif.length },
    ]) {
      await expect(avatars.upload(ADA, file)).rejects.toBeInstanceOf(
        BadRequestException,
      );
    }
    expect(prisma.userAvatar.upsert).not.toHaveBeenCalled();
  });

  it('rejects excessive payloads and dimensions before storing bytes', async () => {
    const { prisma, avatars } = subject();
    await expect(
      avatars.upload(ADA, {
        buffer: Buffer.alloc(MAX_AVATAR_BYTES + 1),
        size: MAX_AVATAR_BYTES + 1,
      }),
    ).rejects.toBeInstanceOf(PayloadTooLargeException);

    const oversized = await image('png', 4097, 1);
    await expect(
      avatars.upload(ADA, { buffer: oversized, size: oversized.length }),
    ).rejects.toBeInstanceOf(BadRequestException);

    expect(prisma.userAvatar.upsert).not.toHaveBeenCalled();
  });

  it('reads stored bytes only, without returning database metadata', async () => {
    const { prisma, avatars } = subject();
    const original = await image('webp');
    prisma.userAvatar.findUnique.mockResolvedValue({
      mimeType: 'image/webp',
      data: new Uint8Array(original),
    });

    const result = await avatars.get(ADA);
    expect(result.mimeType).toBe('image/webp');
    expect(result.data).toEqual(original);
    expect(prisma.userAvatar.findUnique).toHaveBeenCalledWith({
      where: { userId: ADA },
      select: { mimeType: true, data: true },
    });
  });

  it('returns 404 if the avatar is missing', async () => {
    const { prisma, avatars } = subject();
    prisma.userAvatar.findUnique.mockResolvedValue(null);

    await expect(avatars.get(ADA)).rejects.toBeInstanceOf(NotFoundException);
  });

  it('atomically deletes an avatar and resets avatarUrl to null', async () => {
    const { prisma, avatars } = subject();

    await avatars.remove(ADA);

    expect(prisma.$transaction).toHaveBeenCalledOnce();
    expect(prisma.userAvatar.deleteMany).toHaveBeenCalledWith({
      where: { userId: ADA },
    });
    expect(prisma.user.update).toHaveBeenCalledWith({
      where: { id: ADA },
      data: { avatarUrl: null },
    });
  });
});

describe('AvatarOriginGuard', () => {
  const guard = new AvatarOriginGuard();

  function context(headers: Record<string, string>) {
    return {
      switchToHttp: () => ({
        getRequest: () => ({ headers }),
      }),
    } as never;
  }

  it('allows a client without browser-only origin headers', () => {
    expect(guard.canActivate(context({}))).toBe(true);
  });

  it('allows requests from the exact configured application origin', () => {
    const origin = process.env.APP_ORIGIN ?? 'http://localhost:5173';
    expect(
      guard.canActivate(
        context({ origin, 'sec-fetch-site': 'same-origin' }),
      ),
    ).toBe(true);
  });

  it('rejects cross-site metadata, even with no Origin header', () => {
    expect(() => guard.canActivate(context({
      'sec-fetch-site': 'cross-site',
    }))).toThrow('Untrusted request origin');
  });

  it('rejects foreign and malformed Origin headers', () => {
    for (const origin of ['https://evil.invalid', 'not-an-origin']) {
      expect(() => guard.canActivate(context({ origin }))).toThrow(
        'Untrusted request origin',
      );
    }
  });
});
