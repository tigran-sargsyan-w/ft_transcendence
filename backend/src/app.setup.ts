import {
  BadRequestException,
  type INestApplication,
  ValidationPipe,
} from '@nestjs/common';
import {
  ApiExceptionFilter,
  ResponseEnvelopeInterceptor,
} from './shared/api-envelope.js';

export function configureApp(app: INestApplication) {
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
