import { Injectable, NotFoundException } from '@nestjs/common';
import { PUBLIC_USER } from '../auth/auth.service.js';
import { PrismaService } from '../../shared/prisma/prisma.service.js';
import { UpdateProfileDto } from './dto/update-profile.dto.js';

// What any logged-in user may read about another one: never the email.
export const PUBLIC_PROFILE = {
  id: true,
  displayName: true,
  avatarUrl: true,
} as const;

// Prisma's PostgreSQL "contains" uses LIKE/ILIKE pattern matching.
// Escape user input so '%' and '_' are searched literally, not as wildcards.
function escapeLikePattern(value: string) {
  return value.replace(/[\\%_]/g, (character) => '\\' + character);
}

@Injectable()
export class UsersService {
  constructor(private readonly prisma: PrismaService) {}

  searchUsers(userId: string, search: string, limit: number) {
    return this.prisma.user.findMany({
      where: {
        id: { not: userId },
        displayName: {
          contains: escapeLikePattern(search),
          mode: 'insensitive',
        },
      },
      select: PUBLIC_PROFILE,
      orderBy: [{ displayName: 'asc' }, { id: 'asc' }],
      take: limit,
    });
  }

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
