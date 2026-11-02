import {
  BadGatewayException,
  BadRequestException,
  Logger,
  ServiceUnavailableException,
} from '@nestjs/common';
import {
  toEngineTopology,
  topologyRevisionOf,
} from '../topology/engine-topology.js';
import type { TopologySnapshot } from '../topology/graph.types.js';
import { knownSnapshot } from '../topology/known-snapshot.js';
import {
  errorMessage,
  type TopologyService,
} from '../topology/topology.service.js';
import {
  type AnalyzeData,
  type AnalyzeRequest,
  GraphEngineError,
  type GraphEngineClient,
} from './graph-engine-client.js';

export type AnalysisRequest = {
  analyses: string[];
  seedNodeIds?: string[];
  maxDepth?: number;
  maxPaths?: number;
};

// Whether the results describe the topology as it is now:
// - current: the engine echoed the revision it was sent, and it is still the
//   one of the topology;
// - stale: the topology changed while the engine was working;
// - unverifiable: the engine did not echo the revision, so nothing proves
//   which topology the results are about.
// Neither of the last two may be shown as "no risk found".
export type Freshness = 'current' | 'stale' | 'unverifiable';

export type AnalysisResult = {
  environmentId: string;
  // The revision of the topology that was sent to the engine.
  topologyRevision: string;
  analyzedAt: string;
  freshness: Freshness;
  results: AnalyzeData['results'];
};

const freshnessOf = (
  echo: string | undefined,
  current: TopologySnapshot | null,
): Freshness => {
  if (echo === undefined) {
    return 'unverifiable';
  }

  return current && topologyRevisionOf(current) === echo ? 'current' : 'stale';
};

// Runs the analyses of the Graph Engine on the current topology.
export class AnalysisService {
  private readonly logger = new Logger(AnalysisService.name);
  // The calls in progress, by revision and request: the same analysis of the
  // same topology is asked of the engine once, whoever asks.
  private readonly pending = new Map<string, Promise<AnalyzeData>>();

  // `client` is null when no engine is configured: nothing can be analysed.
  constructor(
    private readonly topology: Pick<TopologyService, 'snapshot'>,
    private readonly client: GraphEngineClient | null,
  ) {}

  async analyze(
    environmentId: string,
    request: AnalysisRequest,
  ): Promise<AnalysisResult> {
    const snapshot = knownSnapshot(this.topology.snapshot(), environmentId);
    const topologyRevision = topologyRevisionOf(snapshot);
    // Sorted, so that the order of the caller changes neither the key nor
    // what the engine echoes back.
    const analyses = [...request.analyses].sort();
    const seedNodeIds = request.seedNodeIds && [...request.seedNodeIds].sort();
    const { maxDepth, maxPaths } = request;
    const key = JSON.stringify([
      topologyRevision,
      analyses,
      seedNodeIds,
      maxDepth,
      maxPaths,
    ]);

    let call = this.pending.get(key);

    if (!call) {
      call = this.run(snapshot, {
        analyses,
        options: { seedNodeIds, maxDepth, maxPaths },
      }).finally(() => this.pending.delete(key));
      this.pending.set(key, call);
    }

    const data = await call;

    return {
      environmentId: snapshot.environmentId,
      topologyRevision,
      analyzedAt: data.analyzedAt,
      freshness: freshnessOf(data.topologyRevision, this.topology.snapshot()),
      results: data.results,
    };
  }

  private async run(
    snapshot: TopologySnapshot,
    request: Omit<AnalyzeRequest, 'topology'>,
  ) {
    if (!this.client) {
      this.logger.warn('GRAPH_ENGINE_URL is not set: nothing can be analysed');
      throw this.unavailable();
    }

    try {
      return await this.client.analyze({
        topology: toEngineTopology(snapshot),
        ...request,
      });
    } catch (error) {
      // A request the engine refuses is the caller's mistake: it keeps the
      // engine's code and is not worth a line in the log.
      if (error instanceof GraphEngineError && error.status < 500) {
        throw new BadRequestException(error.message, { errorCode: error.code });
      }

      this.logger.warn(`Graph Engine call failed: ${errorMessage(error)}`);

      // The engine failed on its own, or cannot be used at all.
      throw error instanceof GraphEngineError
        ? new BadGatewayException('The Graph Engine failed', {
            errorCode: 'GRAPH_ENGINE_FAILURE',
          })
        : this.unavailable();
    }
  }

  private unavailable() {
    return new ServiceUnavailableException('The analyses are not available', {
      errorCode: 'ANALYSIS_UNAVAILABLE',
    });
  }
}
