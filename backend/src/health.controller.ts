import {
  Controller,
  Get,
  Logger,
  ServiceUnavailableException,
} from '@nestjs/common';
import { Public } from './modules/auth/auth.decorators.js';
import { pendingMigrations } from './shared/prisma/pending-migrations.js';
import { PrismaService } from './shared/prisma/prisma.service.js';

const unavailable = (details: Record<string, string>) =>
  new ServiceUnavailableException({ message: 'Service unavailable', details });

@Public()
@Controller('health')
export class HealthController {
  private readonly logger = new Logger(HealthController.name);

  constructor(private readonly prisma: PrismaService) {}

  @Get()
  async getHealth() {
    try {
      await this.prisma.$queryRaw`SELECT 1`;
    } catch {
      throw unavailable({ database: 'unavailable' });
    }

    // A database that answers is not enough: with migrations missing, the
    // first request fails. The backend must not look healthy before that.
    let pending: string[];

    try {
      pending = await pendingMigrations(this.prisma);
    } catch (error) {
      // The folder of the migrations cannot be read: nothing can be compared,
      // so the backend cannot say it is healthy.
      this.logger.warn(`Cannot check the migrations: ${String(error)}`);
      throw unavailable({ migrations: 'unavailable' });
    }

    if (pending.length > 0) {
      throw unavailable({ migrations: 'pending' });
    }

    return {
      status: 'ok',
      database: 'ok',
    };
  }
}
