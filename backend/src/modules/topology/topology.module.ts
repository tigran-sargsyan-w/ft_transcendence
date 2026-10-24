import { Module } from '@nestjs/common';
import { HttpCollectorClient } from './collector-client.js';
import { TopologyService } from './topology.service.js';

@Module({
  providers: [
    {
      provide: TopologyService,
      // Without COLLECTOR_URL there is no client, and nothing is built.
      useFactory: () => {
        const url = process.env.COLLECTOR_URL;

        return new TopologyService(url ? new HttpCollectorClient(url) : null);
      },
    },
  ],
  exports: [TopologyService],
})
export class TopologyModule {}
