import {
  type CanActivate,
  type ExecutionContext,
  HttpException,
  HttpStatus,
  Injectable,
} from '@nestjs/common';
import type { Socket } from 'socket.io';

const WINDOW_MS = 10_000;
const MAX_COMMANDS = 10;

// At most 10 commands in 10 s on one connection. It runs before the message
// is validated, so an invalid message counts as well.
@Injectable()
export class CommandLimitGuard implements CanActivate {
  canActivate(context: ExecutionContext) {
    const socket = context.switchToWs().getClient<Socket>();
    const now = Date.now();
    const recent: number[] = (socket.data.commandTimes ?? []).filter(
      (time: number) => now - time < WINDOW_MS,
    );

    socket.data.commandTimes = recent;

    if (recent.length >= MAX_COMMANDS) {
      throw new HttpException(
        'Too many requests',
        HttpStatus.TOO_MANY_REQUESTS,
      );
    }

    recent.push(now);

    return true;
  }
}
