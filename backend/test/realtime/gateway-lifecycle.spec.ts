import type { Socket } from 'socket.io';
import { RealtimeGateway } from '../../src/modules/realtime/realtime.gateway.js';

// The gateway does not use it in this spec, and the real one imports the
// generated Prisma client, which the CI creates only at the build step, after
// the unit tests.
vi.mock('../../src/modules/auth/auth.service.js', () => ({
  AuthService: class AuthService {},
}));

describe('RealtimeGateway connection lifecycle', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  // Only what the lifecycle touches: the session of the socket, and `disconnect`.
  const socketExpiringIn = (ms: number) => {
    const socket = {
      data: {
        session: { tokenHash: 'hash', expiresAt: new Date(Date.now() + ms) },
      },
      disconnect: vi.fn(),
    };

    return { socket, asSocket: socket as unknown as Socket };
  };

  const gateway = () => new RealtimeGateway({} as never, {} as never);

  it('closes a socket when its session expires', () => {
    const { socket, asSocket } = socketExpiringIn(1000);

    gateway().handleConnection(asSocket);
    vi.advanceTimersByTime(1000);

    expect(socket.disconnect).toHaveBeenCalledWith(true);
  });

  it('stops waiting for the expiry of a socket that has disconnected', () => {
    const { socket, asSocket } = socketExpiringIn(1000);
    const subject = gateway();

    subject.handleConnection(asSocket);
    subject.handleDisconnect(asSocket);
    vi.advanceTimersByTime(2000);

    expect(socket.disconnect).not.toHaveBeenCalled();
  });
});
