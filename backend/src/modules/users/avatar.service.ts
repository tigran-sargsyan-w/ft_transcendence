import {
  HttpException,
  Injectable,
  NotFoundException,
  PayloadTooLargeException,
} from '@nestjs/common';
import sharp from 'sharp';
import { validationError } from '../../app.setup.js';
import { PUBLIC_USER } from '../auth/auth.service.js';
import { PrismaService } from '../../shared/prisma/prisma.service.js';

// Bound memory use before image decoding. A decoded 4096x4096 RGBA image
// occupies about 64 MiB; the output is capped at 1024x1024 and 2 MiB.
export const MAX_AVATAR_BYTES = 2 * 1024 * 1024;
const MAX_INPUT_SIDE = 4096;
const MAX_INPUT_PIXELS = MAX_INPUT_SIDE * MAX_INPUT_SIDE;
const OUTPUT_SIDE = 1024;
const AVATAR_MIME = 'image/webp';

export type UploadedAvatar = {
  buffer: Buffer;
  size: number;
};

async function normalizeAvatar(file: UploadedAvatar | undefined) {
  if (!file || !Buffer.isBuffer(file.buffer) || file.size === 0) {
    throw validationError({ file: ['An image file is required'] });
  }

  if (file.size > MAX_AVATAR_BYTES) {
    throw new PayloadTooLargeException('Avatar must not exceed 2 MiB', {
      errorCode: 'AVATAR_TOO_LARGE',
    });
  }

  try {
    const image = sharp(file.buffer, {
      failOn: 'warning',
      limitInputPixels: MAX_INPUT_PIXELS,
      animated: false,
    });
    const metadata = await image.metadata();

    if (
      !metadata.format ||
      !['jpeg', 'png', 'webp'].includes(metadata.format) ||
      !metadata.width ||
      !metadata.height ||
      metadata.width > MAX_INPUT_SIDE ||
      metadata.height > MAX_INPUT_SIDE ||
      (metadata.pages ?? 1) !== 1
    ) {
      throw validationError({
        file: ['Use a single-frame JPEG, PNG or WebP image up to 4096x4096'],
      });
    }

    // Never persist uploaded bytes verbatim. Re-encode to WebP (no EXIF,
    // ICC or embedded metadata) so profiles only serve canonical images.
    const processed = await image
      .rotate()
      .resize({
        width: OUTPUT_SIDE,
        height: OUTPUT_SIDE,
        fit: 'inside',
        withoutEnlargement: true,
      })
      .webp({ quality: 80, effort: 3 })
      .toBuffer();

    if (processed.length > MAX_AVATAR_BYTES) {
      throw new PayloadTooLargeException('Processed avatar exceeds 2 MiB', {
        errorCode: 'AVATAR_TOO_LARGE',
      });
    }

    return new Uint8Array(processed);
  } catch (error) {
    if (error instanceof HttpException) {
      throw error;
    }

    // Do not reveal libvips error details or user-controlled file contents.
    throw validationError({ file: ['Invalid or unsupported image'] });
  }
}

@Injectable()
export class AvatarService {
  constructor(private readonly prisma: PrismaService) {}

  async upload(userId: string, file: UploadedAvatar | undefined) {
    const data = await normalizeAvatar(file);
    const avatarUrl = `/api/v1/users/${userId}/avatar`;

    const [, user] = await this.prisma.$transaction([
      this.prisma.userAvatar.upsert({
        where: { userId },
        create: { userId, data, mimeType: AVATAR_MIME },
        update: { data, mimeType: AVATAR_MIME },
      }),
      this.prisma.user.update({
        where: { id: userId },
        data: { avatarUrl },
        select: PUBLIC_USER,
      }),
    ]);

    return user;
  }

  async get(userId: string) {
    const avatar = await this.prisma.userAvatar.findUnique({
      where: { userId },
      select: { mimeType: true, data: true },
    });

    if (!avatar) {
      throw new NotFoundException('Avatar not found', {
        errorCode: 'AVATAR_NOT_FOUND',
      });
    }

    return {
      mimeType: avatar.mimeType,
      data: Buffer.from(avatar.data),
    };
  }

  async remove(userId: string) {
    await this.prisma.$transaction([
      this.prisma.userAvatar.deleteMany({ where: { userId } }),
      this.prisma.user.update({
        where: { id: userId },
        data: { avatarUrl: null },
      }),
    ]);
  }
}
