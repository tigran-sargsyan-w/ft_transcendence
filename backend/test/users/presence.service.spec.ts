import type { PrismaService } from '../../src/shared/prisma/prisma.service.js';

// The generated Prisma client is not needed for unit tests.
vi.mock('../../src/shared/prisma/prisma.service.js', () => ({
  PrismaService: class PrismaService {},
}));

import {
  OFFLINE_GRACE_MS,
  PresenceService,
} from '../../src/modules/presence/presence.service.js';

const ADA = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
const BOB = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb';
const CAROL = 'cccccccc-cccc-4ccc-8ccc-cccccccccccc';

function subject() {
  const prisma = {
    friendship: { findMany: vi.fn() },
  };
  const presence = new PresenceService(prisma as unknown as PrismaService);
  const events: { userId: string; isOnline: boolean }[] = [];
  const subscription = presence.changes$.subscribe((event) => events.push(event));

  return {
    prisma,
    presence,
    events,
    dispose: () => {
      subscription.unsubscribe();
      presence.onModuleDestroy();
    },
  };
}

describe('PresenceService', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it('becomes online once for any number of tabs and sessions', () => {
    const { presence, events, dispose } = subject();

    presence.connect(ADA, 'tab1', 'session1');
    presence.connect(ADA, 'tab2', 'session1');
    presence.connect(ADA, 'phone', 'session2');
    presence.connect(ADA, 'tab1', 'session1');

    expect(presence.isOnline(ADA)).toBe(true);
    expect(events).toEqual([{ userId: ADA, isOnline: true }]);

    presence.disconnect('tab1');
    presence.disconnect('tab2');

    expect(presence.isOnline(ADA)).toBe(true);
    vi.advanceTimersByTime(OFFLINE_GRACE_MS + 1);
    expect(events).toHaveLength(1);

    presence.disconnect('phone');
    expect(presence.isOnline(ADA)).toBe(true);
    vi.advanceTimersByTime(OFFLINE_GRACE_MS - 1);
    expect(presence.isOnline(ADA)).toBe(true);
    vi.advanceTimersByTime(1);
    expect(presence.isOnline(ADA)).toBe(false);
    expect(events).toEqual([
      { userId: ADA, isOnline: true },
      { userId: ADA, isOnline: false },
    ]);
    dispose();
  });

  it('does not flicker when the last socket reconnects during the grace', () => {
    const { presence, events, dispose } = subject();

    presence.connect(ADA, 'first', 'session1');
    presence.disconnect('first');
    vi.advanceTimersByTime(OFFLINE_GRACE_MS - 100);
    presence.connect(ADA, 'reconnected', 'session1');
    vi.advanceTimersByTime(OFFLINE_GRACE_MS + 1);

    expect(presence.isOnline(ADA)).toBe(true);
    expect(events).toEqual([{ userId: ADA, isOnline: true }]);

    presence.disconnect('reconnected');
    vi.advanceTimersByTime(OFFLINE_GRACE_MS);
    presence.connect(ADA, 'new-session', 'session2');
    expect(events).toEqual([
      { userId: ADA, isOnline: true },
      { userId: ADA, isOnline: false },
      { userId: ADA, isOnline: true },
    ]);
    dispose();
  });

  it('immediately ends a logged-out session but preserves other sessions', () => {
    const { presence, events, dispose } = subject();

    presence.connect(ADA, 'tab', 'session1');
    presence.connect(ADA, 'phone', 'session2');
    presence.endSession('session1');
    presence.disconnect('tab'); // Socket.IO disconnect arrives afterward.

    expect(presence.isOnline(ADA)).toBe(true);
    expect(events).toHaveLength(1);

    presence.endSession('session2');
    expect(presence.isOnline(ADA)).toBe(false);
    expect(events).toEqual([
      { userId: ADA, isOnline: true },
      { userId: ADA, isOnline: false },
    ]);
    dispose();
  });

  it('cancels a grace period if the disconnected session logs out', () => {
    const { presence, events, dispose } = subject();

    presence.connect(ADA, 'tab', 'session1');
    presence.disconnect('tab');
    presence.endSession('session1');

    expect(presence.isOnline(ADA)).toBe(false);
    vi.advanceTimersByTime(OFFLINE_GRACE_MS * 2);
    expect(events).toHaveLength(2);
    dispose();
  });

  it('expires an authenticated socket without waiting for the grace', () => {
    const { presence, events, dispose } = subject();

    presence.connect(ADA, 'tab', 'expired-session');
    presence.disconnect('tab', true);

    expect(presence.isOnline(ADA)).toBe(false);
    expect(events.map(({ isOnline }) => isOnline)).toEqual([true, false]);
    dispose();
  });

  it('checks confirmed friendships on each transition', async () => {
    const { prisma, presence, dispose } = subject();

    prisma.friendship.findMany.mockResolvedValue([
      { userLowId: ADA, userHighId: BOB },
      { userLowId: CAROL, userHighId: ADA },
    ]);

    expect(await presence.acceptedFriendIds(ADA)).toEqual([BOB, CAROL]);
    expect(prisma.friendship.findMany).toHaveBeenCalledWith({
      where: {
        status: 'accepted',
        OR: [{ userLowId: ADA }, { userHighId: ADA }],
      },
      select: { userLowId: true, userHighId: true },
    });
    dispose();
  });

  it('cleans up pending timers when the Nest module shuts down', () => {
    const { presence, events, dispose } = subject();

    presence.connect(BOB, 'tab', 'session');
    presence.disconnect('tab');
    dispose();
    vi.advanceTimersByTime(OFFLINE_GRACE_MS * 2);

    expect(events).toEqual([{ userId: BOB, isOnline: true }]);
  });
});
