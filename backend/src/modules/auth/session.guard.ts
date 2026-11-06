import {
  type CanActivate,
  type ExecutionContext,
  Injectable,
  UnauthorizedException,
} from '@nestjs/common';
import { Reflector } from '@nestjs/core';
import { type AuthenticatedRequest, IS_PUBLIC } from './auth.decorators.js';
import { AuthService } from './auth.service.js';
import { SESSION_COOKIE } from './session-cookie.js';

// Registered globally (APP_GUARD): a new route is protected by default.
@Injectable()
export class SessionGuard implements CanActivate {
  constructor(
    private readonly reflector: Reflector,
    private readonly authService: AuthService,
  ) {}

  async canActivate(context: ExecutionContext) {
    // It also runs for the messages of a gateway, where there is no request
    // and no cookie: a socket is authenticated once, at its handshake.
    if (context.getType() !== 'http') {
      return true;
    }

    const isPublic = this.reflector.getAllAndOverride<boolean>(IS_PUBLIC, [
      context.getHandler(),
      context.getClass(),
    ]);

    if (isPublic) {
      return true;
    }

    const request = context.switchToHttp().getRequest<AuthenticatedRequest>();
    const user = await this.authService.findSessionUser(
      request.cookies?.[SESSION_COOKIE],
    );

    if (!user) {
      throw new UnauthorizedException();
    }

    request.user = user;
    return true;
  }
}
