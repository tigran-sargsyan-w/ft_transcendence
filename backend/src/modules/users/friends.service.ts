import {
  ConflictException,
  Injectable,
  NotFoundException,
} from '@nestjs/common';
import { validationError } from '../../app.setup.js';
import { PrismaService } from '../../shared/prisma/prisma.service.js';
import { PresenceService } from '../presence/presence.service.js';

// Keep the email and password hash out of every friendship response.
const PUBLIC_FRIEND = {
  id: true,
  displayName: true,
  avatarUrl: true,
} as const;

const WITH_USERS = {
  userLow: { select: PUBLIC_FRIEND },
  userHigh: { select: PUBLIC_FRIEND },
} as const;

type FriendUser = {
  id: string;
  displayName: string;
  avatarUrl: string | null;
};

type FriendRow = {
  id: string;
  userLowId: string;
  userHighId: string;
  requestedById: string;
  status: string;
  createdAt: Date;
  acceptedAt: Date | null;
  userLow: FriendUser;
  userHigh: FriendUser;
};

export function friendPair(firstId: string, secondId: string) {
  // PostgreSQL UUID order and normalized hexadecimal string order agree.
  const a = firstId.toLowerCase();
  const b = secondId.toLowerCase();

  return a < b
    ? { userLowId: a, userHighId: b }
    : { userLowId: b, userHighId: a };
}

function otherUser(row: FriendRow, userId: string) {
  return row.userLowId === userId ? row.userHigh : row.userLow;
}

function requestView(row: FriendRow, userId: string) {
  return {
    id: row.id,
    status: 'pending',
    direction: row.requestedById === userId ? 'outgoing' : 'incoming',
    user: otherUser(row, userId),
    createdAt: row.createdAt,
  };
}

const requestNotFound = () =>
  new NotFoundException('Friend request not found', {
    errorCode: 'FRIEND_REQUEST_NOT_FOUND',
  });

const friendNotFound = () =>
  new NotFoundException('Friendship not found', {
    errorCode: 'FRIENDSHIP_NOT_FOUND',
  });

function prismaCode(error: unknown): string | undefined {
  if (error && typeof error === 'object' && 'code' in error) {
    return typeof error.code === 'string' ? error.code : undefined;
  }

  return undefined;
}

@Injectable()
export class FriendsService {
  constructor(
    private readonly prisma: PrismaService,
    private readonly presence: PresenceService,
  ) {}

  async listFriends(userId: string) {
    const rows = await this.prisma.friendship.findMany({
      where: {
        status: 'accepted',
        OR: [{ userLowId: userId }, { userHighId: userId }],
      },
      include: WITH_USERS,
      orderBy: [{ acceptedAt: 'desc' }, { id: 'asc' }],
    });

    return rows.map((row) => {
      const friend = otherUser(row, userId);
      return {
        id: row.id,
        user: friend,
        friendsSince: row.acceptedAt,
        isOnline: this.presence.isOnline(friend.id),
      };
    });
  }

  async listRequests(userId: string) {
    const rows = await this.prisma.friendship.findMany({
      where: {
        status: 'pending',
        OR: [{ userLowId: userId }, { userHighId: userId }],
      },
      include: WITH_USERS,
      orderBy: [{ createdAt: 'desc' }, { id: 'asc' }],
    });

    return {
      incoming: rows
        .filter((row) => row.requestedById !== userId)
        .map((row) => requestView(row, userId)),
      outgoing: rows
        .filter((row) => row.requestedById === userId)
        .map((row) => requestView(row, userId)),
    };
  }

  async sendRequest(userId: string, targetUserId: string) {
    if (userId === targetUserId) {
      throw validationError({
        targetUserId: ['Cannot send a friend request to yourself'],
      });
    }

    const user = await this.prisma.user.findUnique({
      where: { id: targetUserId },
      select: { id: true },
    });

    if (!user) {
      throw new NotFoundException('User not found', {
        errorCode: 'USER_NOT_FOUND',
      });
    }

    try {
      const row = await this.prisma.friendship.create({
        data: {
          ...friendPair(userId, targetUserId),
          requestedById: userId,
        },
        include: WITH_USERS,
      });

      return requestView(row, userId);
    } catch (error) {
      // The unique pair index rejects both duplicate and opposite-direction
      // requests, including two requests created at the same time.
      if (prismaCode(error) === 'P2002') {
        throw new ConflictException('Friend request or friendship already exists', {
          errorCode: 'FRIENDSHIP_ALREADY_EXISTS',
        });
      }

      // The target may have been deleted between the lookup and the insert.
      if (prismaCode(error) === 'P2003') {
        throw new NotFoundException('User not found', {
          errorCode: 'USER_NOT_FOUND',
        });
      }

      throw error;
    }
  }

  async acceptRequest(userId: string, requestId: string) {
    // The requester cannot accept their own request; status and membership
    // are checked inside the write, so simultaneous accepts cannot both win.
    const { count } = await this.prisma.friendship.updateMany({
      where: {
        id: requestId,
        status: 'pending',
        requestedById: { not: userId },
        OR: [{ userLowId: userId }, { userHighId: userId }],
      },
      data: { status: 'accepted', acceptedAt: new Date() },
    });

    if (count === 0) {
      throw requestNotFound();
    }

    const row = await this.prisma.friendship.findUniqueOrThrow({
      where: { id: requestId },
      include: WITH_USERS,
    });

    return {
      id: row.id,
      status: 'accepted',
      user: otherUser(row, userId),
      acceptedAt: row.acceptedAt,
    };
  }

  async deleteRequest(userId: string, requestId: string) {
    // Both the sender and receiver may cancel/decline, but no outsider may.
    const { count } = await this.prisma.friendship.deleteMany({
      where: {
        id: requestId,
        status: 'pending',
        OR: [{ userLowId: userId }, { userHighId: userId }],
      },
    });

    if (count === 0) {
      throw requestNotFound();
    }
  }

  async removeFriend(userId: string, friendId: string) {
    const { count } = await this.prisma.friendship.deleteMany({
      where: { ...friendPair(userId, friendId), status: 'accepted' },
    });

    if (count === 0) {
      throw friendNotFound();
    }
  }
}
