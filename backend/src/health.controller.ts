import { Controller, Get } from '@nestjs/common';

@Controller('api/v1/health')
export class HealthController {
  @Get()
  getHealth(): { data: { status: string; timestamp: string } } {
    return {
      data: {
        status: 'ok',
        timestamp: new Date().toISOString(),
      },
    };
  }
}
