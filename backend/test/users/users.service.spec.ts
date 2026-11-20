import type { PrismaService } from '../../src/shared/prisma/prisma.service.js';

// The Prisma client is generated during the build, after unit tests in CI.
vi.mock('../../src/shared/prisma/prisma.service.js', () => ({
  PrismaService: class PrismaService {},
}));

import { UsersService } from '../../src/modules/users/users.service.js';

const ADA = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
const BOB = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb';

function subject() {
  const prisma = {
    user: {
      findMany: vi.fn(),
      findUnique: vi.fn(),
      update: vi.fn(),
    },
  };

  return {
    prisma,
    users: new UsersService(prisma as unknown as PrismaService),
  };
}

describe('UsersService search', () => {
  it('uses a bounded, case-insensitive query and selects public fields only', async () => {
    const { users, prisma } = subject();
    const bob = { id: BOB, displayName: 'Bob', avatarUrl: null };
    prisma.user.findMany.mockResolvedValue([bob]);

    const results = await users.searchUsers(ADA, 'Bo', 10);

    expect(results).toEqual([bob]);
    expect(prisma.user.findMany).toHaveBeenCalledWith({
      where: {
        id: { not: ADA },
        displayName: { contains: 'Bo', mode: 'insensitive' },
      },
      select: { id: true, displayName: true, avatarUrl: true },
      orderBy: [{ displayName: 'asc' }, { id: 'asc' }],
      take: 10,
    });
    expect(prisma.user.findUnique).not.toHaveBeenCalled();
  });

  it('escapes LIKE wildcards and backslashes instead of widening results', async () => {
    const { users, prisma } = subject();
    prisma.user.findMany.mockResolvedValue([]);

    await users.searchUsers(ADA, '100%_\\', 20);

    expect(prisma.user.findMany).toHaveBeenCalledWith(
      expect.objectContaining({
        where: {
          id: { not: ADA },
          displayName: {
            contains: '100\\%\\_\\\\',
            mode: 'insensitive',
          },
        },
      }),
    );
  });

  it('preserves the existing public profile lookup', async () => {
    const { users, prisma } = subject();
    const bob = { id: BOB, displayName: 'Bob', avatarUrl: null };
    prisma.user.findUnique.mockResolvedValue(bob);

    expect(await users.findProfile(BOB)).toEqual(bob);
    expect(prisma.user.findUnique).toHaveBeenCalledWith({
      where: { id: BOB },
      select: { id: true, displayName: true, avatarUrl: true },
    });
  });
});
