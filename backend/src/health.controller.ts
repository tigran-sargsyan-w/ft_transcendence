import { Controller, Get, ServiceUnavailableException } from '@nestjs/common';
import { Public } from './modules/auth/auth.decorators.js';
import { pendingMigrations } from './shared/prisma/pending-migrations.js';
import { PrismaService } from './shared/prisma/prisma.service.js';

const unavailable = (details: Record<string, string>) =>
  new ServiceUnavailableException({ message: 'Service unavailable', details });

@Public()
@Controller('health')
export class HealthController {
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
    if ((await pendingMigrations(this.prisma)).length > 0) {
      throw unavailable({ migrations: 'pending' });
    }

    return {
      status: 'ok',
      database: 'ok',
    };
  }
}
