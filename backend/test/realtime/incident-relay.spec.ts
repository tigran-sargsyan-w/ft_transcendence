import { EMPTY, Subject } from 'rxjs';
import type { IncidentChange } from '../../src/modules/incidents/incidents.service.js';
import { RealtimeGateway } from '../../src/modules/realtime/realtime.gateway.js';

// The gateway does not use them in this spec, and the real ones import the
// generated Prisma client, which the CI creates only at the build step, after
// the unit tests.
vi.mock('../../src/modules/auth/auth.service.js', () => ({
  AuthService: class AuthService {},
}));
vi.mock('../../src/modules/incidents/incidents.service.js', () => ({
  IncidentsService: class IncidentsService {},
}));

vi.mock('../../src/modules/presence/presence.service.js', () => ({
  PresenceService: class PresenceService {},
}));

const ID = '0b0f6f0e-1111-4222-8333-444455556666';

// A gateway wired to fakes: what it sends is `emit` on the room it names.
function gatewayWithRelay() {
  const changes = new Subject<IncidentChange>();
  const presenceChanges = new Subject<{ userId: string; isOnline: boolean }>();
  const sent: { room: string; event: string; payload: unknown }[] = [];
  const server = {
    engine: { use: vi.fn() },
    use: vi.fn(),
    to: (room: string) => ({
      emit: (event: string, envelope: { payload: unknown }) =>
        sent.push({ room, event, payload: envelope.payload }),
    }),
  };
  const presence = {
    changes$: presenceChanges.asObservable(),
    acceptedFriendIds: vi.fn(async () => ['friend-id']),
    endSession: vi.fn(),
  };
  const gateway = new RealtimeGateway(
    { sessionEnded$: EMPTY } as never,
    { changes$: EMPTY, status$: EMPTY } as never,
    presence as never,
    { changes$: changes.asObservable() } as never,
  );

  // Nest sets `server` (`@WebSocketServer()`) before `afterInit`.
  gateway.server = server as never;
  gateway.afterInit(server as never);

  return { gateway, changes, presenceChanges, sent };
}

const entry = (seq: number) => ({ id: `e${seq}`, seq, incidentId: ID });

describe('RealtimeGateway incident relay', () => {
  it('sends the incident, then its entries in order, to the room of the incident', () => {
    const { changes, sent } = gatewayWithRelay();
    const incident = { id: ID, version: 2 };

    changes.next({
      incidentId: ID,
      incident,
      entries: [entry(5), entry(6)],
    } as never);

    expect(sent).toEqual([
      {
        room: `incident:${ID}`,
        event: 'incident:updated',
        payload: { incidentId: ID, incident },
      },
      {
        room: `incident:${ID}`,
        event: 'timeline:entry_added',
        payload: { incidentId: ID, entry: entry(5) },
      },
      {
        room: `incident:${ID}`,
        event: 'timeline:entry_added',
        payload: { incidentId: ID, entry: entry(6) },
      },
    ]);
  });

  it('sends only the entry of a change that has no incident (a note)', () => {
    const { changes, sent } = gatewayWithRelay();

    changes.next({
      incidentId: ID,
      incident: null,
      entries: [entry(7)],
    } as never);

    expect(sent.map(({ event }) => event)).toEqual(['timeline:entry_added']);
  });

  it('stops relaying when the module is destroyed', () => {
    const { gateway, changes, sent } = gatewayWithRelay();

    gateway.onModuleDestroy();
    changes.next({
      incidentId: ID,
      incident: null,
      entries: [entry(8)],
    } as never);

    expect(sent).toEqual([]);
  });
  it('relays incidents and friend presence independently', async () => {
    const { gateway, changes, presenceChanges, sent } = gatewayWithRelay();
    const incident = { id: ID, version: 2 };

    changes.next({
      incidentId: ID,
      incident,
      entries: [],
    } as never);

    presenceChanges.next({ userId: 'online-user', isOnline: true });

    await vi.waitFor(() => expect(sent).toHaveLength(2));

    expect(sent).toEqual([
      {
        room: `incident:${ID}`,
        event: 'incident:updated',
        payload: { incidentId: ID, incident },
      },
      {
        room: 'user:friend-id',
        event: 'presence:changed',
        payload: { userId: 'online-user', isOnline: true },
      },
    ]);

    gateway.onModuleDestroy();
  });
});
