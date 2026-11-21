import {
  BadRequestException,
  ConflictException,
  NotFoundException,
} from '@nestjs/common';
import type { PrismaService } from '../../src/shared/prisma/prisma.service.js';
import type { PresenceService } from '../../src/modules/presence/presence.service.js';

// The Prisma client is generated during the build, after unit tests in CI.
// Test the service with the database API mocked instead.
vi.mock('../../src/shared/prisma/prisma.service.js', () => ({
  PrismaService: class PrismaService {},
}));

import {
  friendPair,
  FriendsService,
} from '../../src/modules/users/friends.service.js';

const ADA = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
const BOB = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb';
const CAROL = 'cccccccc-cccc-4ccc-8ccc-cccccccccccc';

const ada = { id: ADA, displayName: 'Ada', avatarUrl: null };
const bob = { id: BOB, displayName: 'Bob', avatarUrl: null };

const pending = {
  id: 'dddddddd-dddd-4ddd-8ddd-dddddddddddd',
  userLowId: ADA,
  userHighId: BOB,
  requestedById: ADA,
  status: 'pending',
  createdAt: new Date('2026-11-20T14:00:00Z'),
  acceptedAt: null,
  userLow: ada,
  userHigh: bob,
};

function subject() {
  const prisma = {
    user: { findUnique: vi.fn() },
    friendship: {
      findMany: vi.fn(),
      create: vi.fn(),
      updateMany: vi.fn(),
      findUniqueOrThrow: vi.fn(),
      deleteMany: vi.fn(),
    },
  };

  return {
    prisma,
    friends: new FriendsService(
      prisma as unknown as PrismaService,
      { isOnline: (id: string) => id === BOB } as unknown as PresenceService,
    ),
  };
}

describe('friendPair', () => {
  it('canonically orders two UUIDs, irrespective of request direction or case', () => {
    expect(friendPair(BOB, ADA)).toEqual({
      userLowId: ADA,
      userHighId: BOB,
    });
    expect(friendPair(ADA, BOB.toUpperCase())).toEqual({
      userLowId: ADA,
      userHighId: BOB,
    });
  });
});

describe('FriendsService', () => {
  it('rejects a request to oneself before talking to the database', async () => {
    const { friends, prisma } = subject();

    await expect(friends.sendRequest(ADA, ADA)).rejects.toBeInstanceOf(
      BadRequestException,
    );
    expect(prisma.user.findUnique).not.toHaveBeenCalled();
    expect(prisma.friendship.create).not.toHaveBeenCalled();
  });

  it('rejects an unknown user', async () => {
    const { friends, prisma } = subject();
    prisma.user.findUnique.mockResolvedValue(null);

    await expect(friends.sendRequest(ADA, BOB)).rejects.toBeInstanceOf(
      NotFoundException,
    );
    expect(prisma.friendship.create).not.toHaveBeenCalled();
  });

  it('creates a canonical pending request with public user data', async () => {
    const { friends, prisma } = subject();
    prisma.user.findUnique.mockResolvedValue({ id: ADA });
    prisma.friendship.create.mockResolvedValue({
      ...pending,
      requestedById: BOB,
    });

    const result = await friends.sendRequest(BOB, ADA);

    expect(result).toMatchObject({
      status: 'pending',
      direction: 'outgoing',
      user: ada,
    });
    expect(prisma.friendship.create).toHaveBeenCalledWith(
      expect.objectContaining({
        data: {
          userLowId: ADA,
          userHighId: BOB,
          requestedById: BOB,
        },
      }),
    );
    expect(result.user).not.toHaveProperty('email');
  });

  it('turns a database uniqueness race into a conflict', async () => {
    const { friends, prisma } = subject();
    prisma.user.findUnique.mockResolvedValue({ id: BOB });
    prisma.friendship.create.mockRejectedValue({ code: 'P2002' });

    await expect(friends.sendRequest(ADA, BOB)).rejects.toBeInstanceOf(
      ConflictException,
    );
  });

  it('lists pending requests as incoming and outgoing', async () => {
    const { friends, prisma } = subject();
    const carol = { id: CAROL, displayName: 'Carol', avatarUrl: null };
    const incoming = {
      ...pending,
      id: 'eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee',
      userHighId: CAROL,
      requestedById: CAROL,
      userHigh: carol,
    };
    prisma.friendship.findMany.mockResolvedValue([pending, incoming]);

    const result = await friends.listRequests(ADA);

    expect(result.outgoing).toMatchObject([
      { id: pending.id, direction: 'outgoing', user: bob },
    ]);
    expect(result.incoming).toMatchObject([
      { id: incoming.id, direction: 'incoming', user: carol },
    ]);
    expect(prisma.friendship.findMany).toHaveBeenCalledWith(
      expect.objectContaining({
        where: expect.objectContaining({ status: 'pending' }),
      }),
    );
  });

  it('accepts only a pending request addressed to the current user', async () => {
    const { friends, prisma } = subject();
    prisma.friendship.updateMany.mockResolvedValue({ count: 1 });
    prisma.friendship.findUniqueOrThrow.mockResolvedValue({
      ...pending,
      status: 'accepted',
      acceptedAt: new Date('2026-11-20T14:05:00Z'),
    });

    const accepted = await friends.acceptRequest(BOB, pending.id);

    expect(accepted).toMatchObject({
      status: 'accepted',
      user: ada,
    });
    expect(prisma.friendship.updateMany).toHaveBeenCalledWith({
      where: {
        id: pending.id,
        status: 'pending',
        requestedById: { not: BOB },
        OR: [{ userLowId: BOB }, { userHighId: BOB }],
      },
      data: { status: 'accepted', acceptedAt: expect.any(Date) },
    });
  });

  it('hides unavailable requests and does not read them', async () => {
    const { friends, prisma } = subject();
    prisma.friendship.updateMany.mockResolvedValue({ count: 0 });

    await expect(friends.acceptRequest(CAROL, pending.id)).rejects.toBeInstanceOf(
      NotFoundException,
    );
    expect(prisma.friendship.findUniqueOrThrow).not.toHaveBeenCalled();
  });

  it('cancels only a pending request belonging to the caller', async () => {
    const { friends, prisma } = subject();
    prisma.friendship.deleteMany.mockResolvedValueOnce({ count: 1 });
    prisma.friendship.deleteMany.mockResolvedValueOnce({ count: 0 });

    await friends.deleteRequest(ADA, pending.id);
    expect(prisma.friendship.deleteMany).toHaveBeenCalledWith({
      where: {
        id: pending.id,
        status: 'pending',
        OR: [{ userLowId: ADA }, { userHighId: ADA }],
      },
    });
    await expect(friends.deleteRequest(CAROL, pending.id)).rejects.toBeInstanceOf(
      NotFoundException,
    );
  });

  it('deletes only accepted friendships of the canonical pair', async () => {
    const { friends, prisma } = subject();
    prisma.friendship.deleteMany.mockResolvedValueOnce({ count: 1 });
    prisma.friendship.deleteMany.mockResolvedValueOnce({ count: 0 });

    await friends.removeFriend(BOB, ADA);
    expect(prisma.friendship.deleteMany).toHaveBeenCalledWith({
      where: { userLowId: ADA, userHighId: BOB, status: 'accepted' },
    });
    await expect(friends.removeFriend(BOB, CAROL)).rejects.toBeInstanceOf(
      NotFoundException,
    );
  });

  it('lists accepted friends but never exposes private user fields', async () => {
    const { friends, prisma } = subject();
    prisma.friendship.findMany.mockResolvedValue([
      { ...pending, status: 'accepted', acceptedAt: new Date() },
    ]);

    const result = await friends.listFriends(ADA);

    expect(result).toMatchObject([{ user: bob, isOnline: true }]);
    expect(result[0].user).not.toHaveProperty('email');
    expect(prisma.friendship.findMany).toHaveBeenCalledWith(
      expect.objectContaining({
        where: expect.objectContaining({ status: 'accepted' }),
      }),
    );
  });
});
