import { BadRequestException, ValidationPipe } from '@nestjs/common';
import type { NestExpressApplication } from '@nestjs/platform-express';
import cookieParser from 'cookie-parser';
import {
  ApiExceptionFilter,
  ResponseEnvelopeInterceptor,
} from './shared/api-envelope.js';

// details = { field: [messages] }.
export function validationError(details: Record<string, string[]>) {
  return new BadRequestException(
    { message: 'Request validation failed', details },
    { errorCode: 'VALIDATION_ERROR' },
  );
}

// Also bound to the body of the commands of the realtime gateway.
export function createValidationPipe() {
  return new ValidationPipe({
    whitelist: true,
    forbidNonWhitelisted: true,
    transform: true,
    // Limit: flat DTOs only, a nested field is listed with no messages.
    exceptionFactory: (errors) =>
      validationError(
        Object.fromEntries(
          errors.map((e) => [e.property, Object.values(e.constraints ?? {})]),
        ),
      ),
  });
}

// The app must be created with { bodyParser: false }: only JSON bodies are
// parsed, so a cross-site HTML form cannot post a usable body (ADR 0002).
export function configureApp(app: NestExpressApplication) {
  app.useBodyParser('json');
  app.use(cookieParser());

  app.setGlobalPrefix('api/v1');

  app.useGlobalInterceptors(new ResponseEnvelopeInterceptor());
  app.useGlobalFilters(new ApiExceptionFilter());

  app.useGlobalPipes(createValidationPipe());
}
