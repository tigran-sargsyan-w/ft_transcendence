import { BadRequestException, ValidationPipe } from '@nestjs/common';
import type { NestExpressApplication } from '@nestjs/platform-express';
import cookieParser from 'cookie-parser';
import {
  ApiExceptionFilter,
  ResponseEnvelopeInterceptor,
} from './shared/api-envelope.js';

// The app must be created with { bodyParser: false }: only JSON bodies are
// parsed, so a cross-site HTML form cannot post a usable body (ADR 0002).
export function configureApp(app: NestExpressApplication) {
  app.useBodyParser('json');
  app.use(cookieParser());

  app.setGlobalPrefix('api/v1');

  app.useGlobalInterceptors(new ResponseEnvelopeInterceptor());
  app.useGlobalFilters(new ApiExceptionFilter());

  app.useGlobalPipes(
    new ValidationPipe({
      whitelist: true,
      forbidNonWhitelisted: true,
      transform: true,
      // details = { field: [messages] }.
      // Limit: flat DTOs only, a nested field is listed with no messages.
      exceptionFactory: (errors) =>
        new BadRequestException(
          {
            message: 'Request validation failed',
            details: Object.fromEntries(
              errors.map((e) => [
                e.property,
                Object.values(e.constraints ?? {}),
              ]),
            ),
          },
          { errorCode: 'VALIDATION_ERROR' },
        ),
    }),
  );
}
