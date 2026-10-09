import { Controller, Get, ServiceUnavailableException } from '@nestjs/common';
import { Public } from './modules/auth/auth.decorators.js';
import { PrismaService } from './shared/prisma/prisma.service.js';

@Public()
@Controller('health')
export class HealthController {
  constructor(private readonly prisma: PrismaService) {}

  @Get()
  async getHealth() {
    try {
      await this.prisma.$queryRaw`SELECT 1`;

      return {
        status: 'ok',
        database: 'ok',
      };
    } catch {
      throw new ServiceUnavailableException({
        message: 'Service unavailable',
        details: { database: 'unavailable' },
      });
    }
  }
}
