import type { Socket } from 'socket.io';
import { RealtimeGateway } from '../../src/modules/realtime/realtime.gateway.js';

// The gateway does not use it in this spec, and the real one imports the
// generated Prisma client, which the CI creates only at the build step, after
// the unit tests.
vi.mock('../../src/modules/auth/auth.service.js', () => ({
  AuthService: class AuthService {},
}));
vi.mock('../../src/modules/presence/presence.service.js', () => ({
  PresenceService: class PresenceService {},
}));

vi.mock('../../src/modules/incidents/incidents.service.js', () => ({
  IncidentsService: class IncidentsService {},
}));

describe('RealtimeGateway connection lifecycle', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  // Only what the lifecycle touches: the session of the socket, and `disconnect`.
  const socketExpiringIn = (ms: number) => {
    const socket = {
      id: 'socket-id',
      data: {
        session: { tokenHash: 'hash', expiresAt: new Date(Date.now() + ms) },
        user: { id: 'user-id' },
      },
      disconnect: vi.fn(),
    };

    return { socket, asSocket: socket as unknown as Socket };
  };

  const presence = { connect: vi.fn(), disconnect: vi.fn() };
  const gateway = () =>
    new RealtimeGateway(
      {} as never, {} as never, presence as never, {} as never,
    );

  it('closes a socket when its session expires', () => {
    const { socket, asSocket } = socketExpiringIn(1000);

    gateway().handleConnection(asSocket);
    expect(presence.connect).toHaveBeenCalledWith('user-id', 'socket-id', 'hash');
    vi.advanceTimersByTime(1000);

    expect(socket.disconnect).toHaveBeenCalledWith(true);
    expect(presence.disconnect).toHaveBeenCalledWith('socket-id', true);
  });

  it('stops waiting for the expiry of a socket that has disconnected', () => {
    const { socket, asSocket } = socketExpiringIn(1000);
    const subject = gateway();

    subject.handleConnection(asSocket);
    subject.handleDisconnect(asSocket);
    vi.advanceTimersByTime(2000);

    expect(socket.disconnect).not.toHaveBeenCalled();
    expect(presence.disconnect).toHaveBeenCalledWith('socket-id');
  });
});
