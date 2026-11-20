import { Module } from '@nestjs/common';
import { AuthModule } from '../auth/auth.module.js';
import { IncidentsModule } from '../incidents/incidents.module.js';
import { TopologyModule } from '../topology/topology.module.js';
import { RealtimeGateway } from './realtime.gateway.js';

@Module({
  imports: [AuthModule, IncidentsModule, TopologyModule],
  providers: [RealtimeGateway],
})
export class RealtimeModule {}
