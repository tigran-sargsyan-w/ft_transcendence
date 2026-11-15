import { Injectable, NotFoundException } from '@nestjs/common';
import { PUBLIC_USER } from '../auth/auth.service.js';
import { PrismaService } from '../../shared/prisma/prisma.service.js';
import { UpdateProfileDto } from './dto/update-profile.dto.js';

// What any logged-in user may read about another one: never the email.
const PUBLIC_PROFILE = {
  id: true,
  displayName: true,
  avatarUrl: true,
} as const;

@Injectable()
export class UsersService {
  constructor(private readonly prisma: PrismaService) {}

  async findProfile(id: string) {
    const profile = await this.prisma.user.findUnique({
      where: { id },
      select: PUBLIC_PROFILE,
    });

    if (!profile) {
      throw new NotFoundException('User not found', {
        errorCode: 'USER_NOT_FOUND',
      });
    }

    return profile;
  }

  updateProfile(id: string, dto: UpdateProfileDto) {
    return this.prisma.user.update({
      where: { id },
      data: { displayName: dto.displayName },
      select: PUBLIC_USER,
    });
  }
}
