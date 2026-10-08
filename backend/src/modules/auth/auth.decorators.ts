import {
  createParamDecorator,
  type ExecutionContext,
  SetMetadata,
} from '@nestjs/common';
import type { Request } from 'express';
import type { PublicUser } from './auth.service.js';

export type AuthenticatedRequest = Request & { user?: PublicUser };

export const IS_PUBLIC = 'isPublic';

// Every route needs a session, except those marked @Public().
export const Public = () => SetMetadata(IS_PUBLIC, true);

// The user the session guard attached to the request.
export const CurrentUser = createParamDecorator(
  (_data: unknown, context: ExecutionContext) =>
    context.switchToHttp().getRequest<AuthenticatedRequest>().user,
);
