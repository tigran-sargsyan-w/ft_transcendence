import { Injectable, type OnModuleDestroy } from '@nestjs/common';
import { Subject } from 'rxjs';
import { PrismaService } from '../../shared/prisma/prisma.service.js';

export type PresenceChange = { userId: string; isOnline: boolean };

// A transport interruption should not make a friend flicker offline while
// Socket.IO reconnects. Logout and session expiry bypass this delay.
export const OFFLINE_GRACE_MS = 5_000;

type Connection = { userId: string; sessionHash: string };
type UserState = {
  sockets: Set<string>;
  offlineTimer?: NodeJS.Timeout;
  disconnectedSessions: Set<string>;
};

@Injectable()
export class PresenceService implements OnModuleDestroy {
  private readonly connections = new Map<string, Connection>();
  private readonly users = new Map<string, UserState>();
  private readonly changes = new Subject<PresenceChange>();

  readonly changes$ = this.changes.asObservable();

  constructor(private readonly prisma: PrismaService) {}

  // The identity and session hash come exclusively from the authenticated
  // Socket.IO handshake, never from a client-submitted command.
  connect(userId: string, socketId: string, sessionHash: string) {
    if (this.connections.has(socketId)) {
      return;
    }

    let state = this.users.get(userId);
    if (!state) {
      state = { sockets: new Set(), disconnectedSessions: new Set() };
      this.users.set(userId, state);
      this.changes.next({ userId, isOnline: true });
    }

    if (state.offlineTimer) {
      clearTimeout(state.offlineTimer);
      state.offlineTimer = undefined;
    }

    state.disconnectedSessions.clear();
    state.sockets.add(socketId);
    this.connections.set(socketId, { userId, sessionHash });
  }

  disconnect(socketId: string, immediate = false) {
    const connection = this.connections.get(socketId);
    if (!connection) {
      return;
    }

    this.connections.delete(socketId);
    const state = this.users.get(connection.userId);
    if (!state) {
      return;
    }

    state.sockets.delete(socketId);
    state.disconnectedSessions.add(connection.sessionHash);

    if (state.sockets.size > 0) {
      return;
    }

    if (immediate) {
      this.finishOffline(connection.userId);
      return;
    }

    // No timer should survive a renewed connection, or be duplicated by a
    // repeated disconnect. The user remains online throughout the grace.
    if (!state.offlineTimer) {
      state.offlineTimer = setTimeout(
        () => this.finishOffline(connection.userId),
        OFFLINE_GRACE_MS,
      );
    }
  }

  // A logout invalidates one session, not the user's other active sessions.
  // Also handles a session whose last socket is already in the grace period.
  endSession(sessionHash: string) {
    for (const [socketId, connection] of this.connections) {
      if (connection.sessionHash === sessionHash) {
        this.disconnect(socketId, true);
      }
    }

    for (const [userId, state] of this.users) {
      if (
        state.sockets.size === 0 &&
        state.disconnectedSessions.has(sessionHash)
      ) {
        this.finishOffline(userId);
      }
    }
  }

  isOnline(userId: string) {
    return this.users.has(userId);
  }

  // Resolve recipients from the database on EVERY transition. Neither
  // pending requests nor removed friendships authorize presence events.
  async acceptedFriendIds(userId: string): Promise<string[]> {
    const friendships = await this.prisma.friendship.findMany({
      where: {
        status: 'accepted',
        OR: [{ userLowId: userId }, { userHighId: userId }],
      },
      select: { userLowId: true, userHighId: true },
    });

    return friendships.map((friendship) =>
      friendship.userLowId === userId
        ? friendship.userHighId
        : friendship.userLowId,
    );
  }

  onModuleDestroy() {
    for (const state of this.users.values()) {
      if (state.offlineTimer) {
        clearTimeout(state.offlineTimer);
      }
    }

    this.connections.clear();
    this.users.clear();
    this.changes.complete();
  }

  private finishOffline(userId: string) {
    const state = this.users.get(userId);
    if (!state || state.sockets.size > 0) {
      return;
    }

    if (state.offlineTimer) {
      clearTimeout(state.offlineTimer);
    }

    this.users.delete(userId);
    this.changes.next({ userId, isOnline: false });
  }
}
