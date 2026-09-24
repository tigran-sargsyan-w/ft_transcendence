import {
  type CallHandler,
  type ExecutionContext,
  Injectable,
  type NestInterceptor,
  StreamableFile,
} from '@nestjs/common';
import { map } from 'rxjs';

// Controllers return the raw value; this is the only place that adds `data`.
@Injectable()
export class ResponseEnvelopeInterceptor implements NestInterceptor {
  intercept(_context: ExecutionContext, next: CallHandler) {
    return next
      .handle()
      .pipe(map((data) => (data instanceof StreamableFile ? data : { data })));
  }
}
