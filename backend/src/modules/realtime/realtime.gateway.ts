import {
  ForbiddenException,
  Logger,
  UnauthorizedException,
} from '@nestjs/common';
import { type OnGatewayInit, WebSocketGateway } from '@nestjs/websockets';
import cookieParser from 'cookie-parser';
import type { Request } from 'express';
import type { Server } from 'socket.io';
import { toErrorResponse } from '../../shared/api-envelope.js';
import { AuthService } from '../auth/auth.service.js';
import { SESSION_COOKIE } from '../auth/session-cookie.js';

// Socket.IO on the path of its default (/socket.io/), on the port of the API.
@WebSocketGateway()
export class RealtimeGateway implements OnGatewayInit {
  private readonly logger = new Logger(RealtimeGateway.name);

  constructor(private readonly authService: AuthService) {}

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
        next();
      } catch (error) {
        // `connect_error` on the client, with `error.data = { code, message }`.
        const { code, message } = toErrorResponse(error, this.logger).body
          .error;

        next(Object.assign(new Error(message), { data: { code, message } }));
      }
    });
  }
}
