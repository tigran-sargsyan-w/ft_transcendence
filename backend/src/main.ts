import 'reflect-metadata';
import { NestFactory } from '@nestjs/core';
import { AppModule } from './app.module';

async function bootstrap(): Promise<void> {
  const app = await NestFactory.create(AppModule);
  const port = Number(process.env.PORT ?? 3000);
  const frontendOrigin = process.env.FRONTEND_ORIGIN ?? 'http://localhost:5173';

  app.enableCors({
    origin: frontendOrigin,
    credentials: true,
  });

  await app.listen(port, '0.0.0.0');
}

void bootstrap();
