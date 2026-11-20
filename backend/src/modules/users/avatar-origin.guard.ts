import {
  CanActivate,
  ExecutionContext,
  ForbiddenException,
  Injectable,
} from '@nestjs/common';
import type { Request } from 'express';

// Multipart can be submitted by an HTML form. Validate browser origin and
// Fetch Metadata before Multer reads the request, while still permitting
// local API clients (Postman, E2E) which send neither browser header.
@Injectable()
export class AvatarOriginGuard implements CanActivate {
  canActivate(context: ExecutionContext) {
    const request = context.switchToHttp().getRequest<Request>();
    const origin = request.headers.origin;
    const fetchSite = request.headers['sec-fetch-site'];

    if (fetchSite && fetchSite !== 'same-origin' && fetchSite !== 'none') {
      throw new ForbiddenException('Untrusted request origin', {
        errorCode: 'INVALID_ORIGIN',
      });
    }

    if (origin !== undefined) {
      const expected = process.env.APP_ORIGIN ?? 'http://localhost:5173';

      try {
        if (new URL(origin).origin === new URL(expected).origin) {
          return true;
        }
      } catch {
        // Malformed origins are denied, not treated as missing.
      }

      throw new ForbiddenException('Untrusted request origin', {
        errorCode: 'INVALID_ORIGIN',
      });
    }

    return true;
  }
}
