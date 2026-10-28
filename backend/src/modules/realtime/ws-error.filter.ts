import {
  type ArgumentsHost,
  Catch,
  type ExceptionFilter,
  Logger,
} from '@nestjs/common';
import { toErrorResponse } from '../../shared/api-envelope.js';

// A command answers in its acknowledgement, in the shape of the HTTP API. The
// answer is sent here: once a filter has dealt with an exception, nothing is
// returned, and Nest never calls the acknowledgement by itself. The global
// filter of the API does not reach a gateway.
@Catch()
export class WsErrorFilter implements ExceptionFilter {
  private readonly logger = new Logger(WsErrorFilter.name);

  catch(exception: unknown, host: ArgumentsHost) {
    const { body } = toErrorResponse(exception, this.logger);
    // The arguments of a handler: the socket, the data, the acknowledgement.
    // A client that emitted without a callback has none.
    const ack = host.getArgByIndex(2);

    if (typeof ack === 'function') {
      ack(body);
    }
  }
}
