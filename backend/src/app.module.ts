import { Module } from '@nestjs/common';
import { HealthController } from './health.controller.js';
import { AuthModule } from './modules/auth/auth.module.js';
import { TopologyModule } from './modules/topology/topology.module.js';
import { PrismaModule } from './shared/prisma/prisma.module.js';

@Module({
  imports: [PrismaModule, AuthModule, TopologyModule],
  controllers: [HealthController],
})
export class AppModule {}
