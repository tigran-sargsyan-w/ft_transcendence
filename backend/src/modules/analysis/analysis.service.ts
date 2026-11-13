import {
  BadGatewayException,
  Logger,
  ServiceUnavailableException,
} from '@nestjs/common';
import { validationError } from '../../app.setup.js';
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
import { NEEDS_SEEDS, RESULT_KEYS } from './analyses.js';
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

// Whether the results describe the infrastructure as it is now:
// - current: the topology is still at the revision that was sent, and the
//   collector is reachable;
// - stale: the topology changed while the engine was working, or the
//   collector has been unreachable for a while, so the topology is the last
//   one known and not necessarily the live one;
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

// The ids that are not nodes of the topology are the user's input: they are
// said here, under the name `seedNodeIds`. The incidents check theirs the same
// way.
export function checkNodeIds(snapshot: TopologySnapshot, ids: string[]) {
  const nodes = new Set(snapshot.nodes.map((node) => node.id));
  const unknown = ids.filter((id) => !nodes.has(id));

  if (unknown.length > 0) {
    throw validationError({
      seedNodeIds: unknown.map((id) => `${id} is not a node of the topology`),
    });
  }
}

// `echoed`: the engine sent the revision back, and `run` has checked that it
// is the one it was sent.
// The seeds are the user's input, so what is wrong with them is said here,
// with the name of the field, now that the topology is at hand. Whatever the
// engine still refuses after this is not the user's doing.
function checkSeeds(
  snapshot: TopologySnapshot,
  analyses: string[],
  seedNodeIds: string[] | undefined,
) {
  if (!seedNodeIds?.length) {
    if (analyses.some((id) => NEEDS_SEEDS.includes(id))) {
      throw validationError({
        seedNodeIds: [
          `seedNodeIds is required for ${NEEDS_SEEDS.join(' and ')}`,
        ],
      });
    }

    return;
  }

  checkNodeIds(snapshot, seedNodeIds);
}

// The result of an analysis, if the engine sent one.
const hasResult = (results: AnalyzeData['results'], id: string) => {
  const result = results[(RESULT_KEYS as Record<string, string>)[id]];

  return typeof result === 'object' && result !== null;
};

const freshnessOf = (
  echoed: boolean,
  sent: string,
  current: TopologySnapshot | null,
  collectorUnreachable: boolean,
): Freshness => {
  if (!echoed) {
    return 'unverifiable';
  }

  const unchanged = current !== null && topologyRevisionOf(current) === sent;

  return unchanged && !collectorUnreachable ? 'current' : 'stale';
};

// Runs the analyses of the Graph Engine on the current topology.
export class AnalysisService {
  private readonly logger = new Logger(AnalysisService.name);
  // The calls in progress, by revision and request: while one is running, the
  // same analysis of the same topology waits for it instead of starting
  // another.
  private readonly pending = new Map<string, Promise<AnalyzeData>>();

  // `client` is null when no engine is configured: nothing can be analysed.
  constructor(
    private readonly topology: Pick<TopologyService, 'snapshot' | 'status'>,
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

    checkSeeds(snapshot, analyses, seedNodeIds);
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
      freshness: freshnessOf(
        data.topologyRevision !== undefined,
        topologyRevision,
        this.topology.snapshot(),
        this.topology.status().stale,
      ),
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
      const data = await this.client.analyze({
        topology: toEngineTopology(snapshot),
        ...request,
      });
      const sent = topologyRevisionOf(snapshot);

      // The answer must be about what was sent. An echo of another revision,
      // even the current one, or of another environment, says nothing about
      // this topology.
      if (
        data.environmentId !== snapshot.environmentId ||
        (data.topologyRevision !== undefined && data.topologyRevision !== sent)
      ) {
        throw new Error(
          `The Graph Engine answered about ${data.environmentId} at ${data.topologyRevision}, not ${snapshot.environmentId} at ${sent}`,
        );
      }

      // Every analysis that was asked for must have its result: an answer
      // without one would read as "nothing found".
      const missing = request.analyses.filter(
        (id) => !hasResult(data.results, id),
      );

      if (missing.length > 0) {
        throw new Error(
          `The Graph Engine sent no result for ${missing.join(', ')}`,
        );
      }

      return data;
    } catch (error) {
      this.logger.warn(
        `Graph Engine call failed: ${
          error instanceof GraphEngineError
            ? `answered ${error.status} ${error.code}: ${error.message}`
            : errorMessage(error)
        }`,
      );

      // The engine failed on its own, or refused a request that was valid
      // here (a topology it does not accept, a version that moved on): never
      // the user's doing. Or it cannot be used at all.
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
