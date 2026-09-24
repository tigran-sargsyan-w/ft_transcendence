import { type INestApplication, ValidationPipe } from '@nestjs/common';
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
    }),
  );
}
