import {
  Controller,
  Delete,
  Get,
  HttpCode,
  Param,
  ParseUUIDPipe,
  Post,
  Res,
  UploadedFile,
  UseGuards,
  UseInterceptors,
} from '@nestjs/common';
import { FileInterceptor } from '@nestjs/platform-express';
import { Throttle, ThrottlerGuard } from '@nestjs/throttler';
import type { Response } from 'express';
import { CurrentUser } from '../auth/auth.decorators.js';
import type { PublicUser } from '../auth/auth.service.js';
import { AvatarOriginGuard } from './avatar-origin.guard.js';
import { AvatarService, MAX_AVATAR_BYTES, type UploadedAvatar } from './avatar.service.js';

@Controller('users')
export class AvatarController {
  constructor(private readonly avatars: AvatarService) {}

  @Post('me/avatar')
  @UseGuards(AvatarOriginGuard, ThrottlerGuard)
  @Throttle({ default: { limit: 10, ttl: 60_000 } })
  @UseInterceptors(FileInterceptor('file', {
    limits: {
      fileSize: MAX_AVATAR_BYTES,
      files: 1,
      fields: 0,
      parts: 1,
    },
  }))
  upload(
    @CurrentUser() user: PublicUser,
    @UploadedFile() file: UploadedAvatar | undefined,
  ) {
    return this.avatars.upload(user.id, file);
  }

  // Respond with actual image bytes, never the standard JSON data envelope.
  @Get(':id/avatar')
  async get(
    @Param('id', ParseUUIDPipe) id: string,
    @Res() response: Response,
  ) {
    const avatar = await this.avatars.get(id);

    response.set({
      'Content-Type': avatar.mimeType,
      'Cache-Control': 'private, no-store',
      'X-Content-Type-Options': 'nosniff',
      'Content-Security-Policy': "default-src 'none'; sandbox",
    });
    response.status(200).send(avatar.data);
  }

  @Delete('me/avatar')
  @HttpCode(204)
  @UseGuards(AvatarOriginGuard, ThrottlerGuard)
  @Throttle({ default: { limit: 10, ttl: 60_000 } })
  remove(@CurrentUser() user: PublicUser) {
    return this.avatars.remove(user.id);
  }
}
