import { Module } from '@nestjs/common';
import { AuthModule } from '../auth/auth.module.js';
import { TopologyModule } from '../topology/topology.module.js';
import { RealtimeGateway } from './realtime.gateway.js';

@Module({
  imports: [AuthModule, TopologyModule],
  providers: [RealtimeGateway],
})
export class RealtimeModule {}
