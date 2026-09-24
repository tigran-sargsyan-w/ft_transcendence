import { type INestApplication, ValidationPipe } from '@nestjs/common';
import { ResponseEnvelopeInterceptor } from './shared/api-envelope.js';

export function configureApp(app: INestApplication) {
  app.setGlobalPrefix('api/v1');

  app.useGlobalInterceptors(new ResponseEnvelopeInterceptor());

  app.useGlobalPipes(
    new ValidationPipe({
      whitelist: true,
      forbidNonWhitelisted: true,
      transform: true,
    }),
  );
}
