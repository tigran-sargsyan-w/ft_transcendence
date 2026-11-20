import {
  ConflictException,
  ForbiddenException,
  Logger,
  type OnModuleDestroy,
  UnauthorizedException,
  UseFilters,
  UseGuards,
} from '@nestjs/common';
import {
  Ack,
  ConnectedSocket,
  MessageBody,
  type OnGatewayConnection,
  type OnGatewayDisconnect,
  type OnGatewayInit,
  SubscribeMessage,
  WebSocketGateway,
  WebSocketServer,
} from '@nestjs/websockets';
import cookieParser from 'cookie-parser';
import type { Request } from 'express';
import type { Subscription } from 'rxjs';
import type { Server, Socket } from 'socket.io';
import { createValidationPipe, validationError } from '../../app.setup.js';
import { toErrorResponse } from '../../shared/api-envelope.js';
import { AuthService } from '../auth/auth.service.js';
import { SESSION_COOKIE } from '../auth/session-cookie.js';
import {
  type IncidentChange,
  IncidentsService,
} from '../incidents/incidents.service.js';
import type { TopologySnapshot } from '../topology/graph.types.js';
import { knownSnapshot } from '../topology/known-snapshot.js';
import {
  type TopologyChange,
  TopologyService,
} from '../topology/topology.service.js';
import { CommandLimitGuard } from './command-limit.guard.js';
import { IncidentRoomDto } from './dto/incident-room.dto.js';
import { SubscribeDto } from './dto/subscribe.dto.js';
import { disconnectAt } from './session-expiry.js';
import { WsErrorFilter } from './ws-error.filter.js';

type AckFn = (response: { data: unknown } | { error: unknown }) => void;

// One room per environment.
const room = (environmentId: string) => `env:${environmentId}`;

// One room per incident. The id is the one the database gives, in small
// letters: the same name for a join, a leave and what the server sends.
const incidentRoom = (incidentId: string) => `incident:${incidentId}`;

// One room per session, to close its sockets when it ends. It is named after
// the hash of the session token, which is never sent to a client.
const sessionRoom = (tokenHash: string) => `session:${tokenHash}`;

// What the server sends: the envelope of docs/architecture/realtime-conventions.md.
function send(
  target: { emit: (event: string, ...args: unknown[]) => unknown },
  event: string,
  payload: unknown,
) {
  target.emit(event, { event, payload, timestamp: new Date().toISOString() });
}

// The messages of docs/architecture/realtime-topology.md, on Socket.IO's
// default path (/socket.io/) and the port of the API. A message over 16 KB
// closes the socket.
@WebSocketGateway({ maxHttpBufferSize: 16 * 1024 })
@UseGuards(CommandLimitGuard)
@UseFilters(WsErrorFilter)
export class RealtimeGateway
  implements
    OnGatewayInit,
    OnGatewayConnection,
    OnGatewayDisconnect,
    OnModuleDestroy
{
  @WebSocketServer() server!: Server;

  private readonly logger = new Logger(RealtimeGateway.name);
  private readonly relays: Subscription[] = [];
  // Known from the first snapshot: a delta does not name its environment.
  private environmentId: string | null = null;

  constructor(
    private readonly authService: AuthService,
    private readonly topology: TopologyService,
    private readonly incidents: IncidentsService,
  ) {}

  afterInit(server: Server) {
    // Fills `request.cookies` for the handshake request, as for the API routes.
    server.engine.use(cookieParser());

    server.use(async (socket, next) => {
      try {
        // A browser always names the page it comes from. No Origin at all is
        // a client that is not a browser, or the polling transport, a GET of
        // the same origin, which does not send it.
        const { origin } = socket.handshake.headers;

        if (origin !== undefined && origin !== process.env.APP_ORIGIN) {
          throw new ForbiddenException();
        }

        const session = await this.authService.findSession(
          (socket.request as Request).cookies?.[SESSION_COOKIE],
        );

        if (!session) {
          throw new UnauthorizedException();
        }

        socket.data.user = session.user;
        socket.data.session = {
          tokenHash: session.tokenHash,
          expiresAt: session.expiresAt,
        };
        await socket.join(sessionRoom(session.tokenHash));
        next();
      } catch (error) {
        // `connect_error` on the client, with `error.data = { code, message }`.
        const { code, message } = toErrorResponse(error, this.logger).body
          .error;

        next(Object.assign(new Error(message), { data: { code, message } }));
      }
    });

    // What the topology publishes goes to the room in the same step, so no
    // change falls between the snapshot a socket gets and what follows it.
    this.relays.push(
      this.topology.changes$.subscribe((change) => this.relay(change)),
      this.topology.status$.subscribe((status) =>
        this.toRoom('topology:status', status),
      ),
      // What the incidents service has committed goes to the room of the incident.
      this.incidents.changes$.subscribe((change) => this.relayIncident(change)),
      // A logout closes the sockets of that session at once.
      this.authService.sessionEnded$.subscribe((tokenHash) =>
        this.server.in(sessionRoom(tokenHash)).disconnectSockets(true),
      ),
    );
  }

  onModuleDestroy() {
    this.relays.forEach((relay) => relay.unsubscribe());
  }

  // A socket lasts as long as its session: it is closed at the expiry date,
  // unless a logout closes it before. The client then receives `disconnect`
  // with the reason `io server disconnect`.
  handleConnection(socket: Socket) {
    socket.data.stopExpiry = disconnectAt(socket.data.session.expiresAt, () =>
      socket.disconnect(true),
    );
  }

  handleDisconnect(socket: Socket) {
    socket.data.stopExpiry?.();
  }

  @SubscribeMessage('topology:subscribe')
  async subscribe(
    @ConnectedSocket() socket: Socket,
    // The pipe is on the body only: the socket is not something to validate.
    @MessageBody(createValidationPipe()) body: SubscribeDto,
    @Ack() ack?: AckFn,
  ) {
    // A primitive gets through the pipe: it has no property to check.
    if (typeof body !== 'object') {
      throw validationError({ body: ['body must be an object'] });
    }

    const known = knownSnapshot(this.topology.snapshot(), body.environmentId);
    const { environmentId } = known;

    // The room first, then the snapshot. Joining may not be immediate, and a
    // change published while it lasts is in the snapshot read just after.
    this.environmentId = environmentId;
    await socket.join(room(environmentId));

    // No await from here on: nothing can be published between the read of the
    // snapshot and its sending, and what comes later reaches the room.
    this.answer(socket, ack, this.topology.snapshot() ?? known);
  }

  @SubscribeMessage('topology:unsubscribe')
  async unsubscribe(@ConnectedSocket() socket: Socket, @Ack() ack?: AckFn) {
    if (this.environmentId) {
      await socket.leave(room(this.environmentId));
    }

    ack?.({ data: {} });
  }

  @SubscribeMessage('topology:resync')
  resync(@ConnectedSocket() socket: Socket, @Ack() ack?: AckFn) {
    const snapshot = this.topology.snapshot();

    if (!snapshot || !socket.rooms.has(room(snapshot.environmentId))) {
      throw new ConflictException('Not subscribed to the topology', {
        errorCode: 'TOPOLOGY_NOT_SUBSCRIBED',
      });
    }

    this.answer(socket, ack, snapshot);
  }

  @SubscribeMessage('incident:join')
  async joinIncident(
    @ConnectedSocket() socket: Socket,
    @MessageBody(createValidationPipe()) body: IncidentRoomDto,
    @Ack() ack?: AckFn,
  ) {
    // In v1 every signed-in user may join any incident.
    const incidentId = await this.incidents.findId(body.incidentId);

    await socket.join(incidentRoom(incidentId));
    ack?.({ data: { incidentId } });
  }

  @SubscribeMessage('incident:leave')
  async leaveIncident(
    @ConnectedSocket() socket: Socket,
    @MessageBody(createValidationPipe()) body: IncidentRoomDto,
    @Ack() ack?: AckFn,
  ) {
    // No lookup: leaving a room one is not in does nothing.
    await socket.leave(incidentRoom(body.incidentId.toLowerCase()));
    ack?.({ data: {} });
  }

  // The acknowledgement, then the snapshot, then the status: Socket.IO keeps
  // the order of what it sends on one connection.
  private answer(
    socket: Socket,
    ack: AckFn | undefined,
    snapshot: TopologySnapshot,
  ) {
    ack?.({ data: { environmentId: snapshot.environmentId } });
    send(socket, 'topology:snapshot', snapshot);
    send(socket, 'topology:status', this.topology.status());
  }

  private relay(change: TopologyChange) {
    if (change.type === 'snapshot') {
      this.environmentId = change.snapshot.environmentId;
      this.toRoom('topology:snapshot', change.snapshot);
    } else {
      this.toRoom('topology:delta', change.delta);
    }
  }

  // The incident first, then its new entries in the order of the timeline.
  private relayIncident({ incidentId, incident, entries }: IncidentChange) {
    const target = this.server.to(incidentRoom(incidentId));

    if (incident) {
      send(target, 'incident:updated', { incidentId, incident });
    }

    for (const entry of entries) {
      send(target, 'timeline:entry_added', { incidentId, entry });
    }
  }

  private toRoom(event: string, payload: unknown) {
    if (this.environmentId) {
      send(this.server.to(room(this.environmentId)), event, payload);
    }
  }
}
