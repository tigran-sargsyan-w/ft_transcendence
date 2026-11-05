import { Module } from '@nestjs/common';
import { TopologyService } from '../topology/topology.service.js';
import { TopologyModule } from '../topology/topology.module.js';
import { AnalysisController } from './analysis.controller.js';
import { AnalysisService } from './analysis.service.js';
import { HttpGraphEngineClient } from './graph-engine-client.js';

@Module({
  imports: [TopologyModule],
  controllers: [AnalysisController],
  providers: [
    {
      provide: AnalysisService,
      inject: [TopologyService],
      // Without GRAPH_ENGINE_URL there is no client, and every analysis
      // answers ANALYSIS_UNAVAILABLE.
      useFactory: (topology: TopologyService) => {
        const url = process.env.GRAPH_ENGINE_URL;

        return new AnalysisService(
          topology,
          url ? new HttpGraphEngineClient(url) : null,
        );
      },
    },
  ],
})
export class AnalysisModule {}
