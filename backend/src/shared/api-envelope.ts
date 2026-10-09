import {
  type ArgumentsHost,
  type CallHandler,
  Catch,
  type ExceptionFilter,
  type ExecutionContext,
  HttpException,
  HttpStatus,
  Injectable,
  Logger,
  type NestInterceptor,
  StreamableFile,
} from '@nestjs/common';
import type { Response } from 'express';
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

// What body-parser throws (413 too large...): a plain Error with a 4xx `status`.
// `expose` is true only when the message is safe to show to the client.
type HttpError = Error & { status?: number; expose?: boolean };

// Every error leaves as { error: { code, message } }. The code is the
// exception's `errorCode`, else its HTTP status name (404 -> NOT_FOUND).
@Catch()
export class ApiExceptionFilter implements ExceptionFilter {
  private readonly logger = new Logger(ApiExceptionFilter.name);

  catch(exception: unknown, host: ArgumentsHost) {
    const response = host.switchToHttp().getResponse<Response>();

    if (exception instanceof HttpException) {
      const status = exception.getStatus();
      // Optional `details`: set by the validation factory and by /health.
      const { details } = exception.getResponse() as { details?: unknown };

      response.status(status).json({
        error: {
          code: exception.errorCode ?? HttpStatus[status],
          message: exception.message,
          details,
        },
      });
      return;
    }

    if (exception instanceof Error) {
      const { status, expose } = exception as HttpError;

      if (expose && status) {
        response.status(status).json({
          error: { code: HttpStatus[status], message: exception.message },
        });
        return;
      }
    }

    // Unknown error: log the real one, send nothing that could leak.
    this.logger.error(exception);

    response.status(HttpStatus.INTERNAL_SERVER_ERROR).json({
      error: { code: 'INTERNAL_ERROR', message: 'Internal server error' },
    });
  }
}
