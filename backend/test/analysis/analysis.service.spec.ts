import {
  BadGatewayException,
  BadRequestException,
  Logger,
  NotFoundException,
  ServiceUnavailableException,
} from '@nestjs/common';
import { AnalysisService } from '../../src/modules/analysis/analysis.service.js';
import {
  type AnalyzeData,
  type AnalyzeRequest,
  type GraphEngineClient,
  GraphEngineError,
} from '../../src/modules/analysis/graph-engine-client.js';
import { buildGraph } from '../../src/modules/topology/build-graph.js';
import { toEngineTopology } from '../../src/modules/topology/engine-topology.js';
import type { TopologySnapshot } from '../../src/modules/topology/graph.types.js';
import { ENVIRONMENT_ID, api, db, web } from '../topology/fixtures.js';

const graph = buildGraph(ENVIRONMENT_ID, [db(), api(), web()]);

const snapshotAt = (revision: number): TopologySnapshot => ({
  schemaVersion: 1,
  environmentId: ENVIRONMENT_ID,
  capturedAt: '2026-11-02T10:00:00.000Z',
  epoch: 'epoch-a',
  revision,
  ...graph,
});

// What a well behaved engine answers: the revision it was sent, echoed.
const echoing = (request: AnalyzeRequest): AnalyzeData => ({
  schemaVersion: 1,
  environmentId: request.topology.environmentId,
  topologyRevision: request.topology.topologyRevision,
  analyzedAt: '2026-11-02T10:00:01Z',
  results: { criticalNodes: { nodes: [] } },
});

class FakeEngine implements GraphEngineClient {
  readonly calls: AnalyzeRequest[] = [];

  constructor(
    private readonly answer: (request: AnalyzeRequest) => Promise<AnalyzeData>,
  ) {}

  analyze(request: AnalyzeRequest) {
    this.calls.push(request);

    return this.answer(request);
  }
}

describe('AnalysisService', () => {
  let current: TopologySnapshot | null;
  const topology = { snapshot: () => current };
  const ask = { analyses: ['critical_nodes'] };

  beforeEach(() => {
    current = snapshotAt(1);
    vi.spyOn(Logger.prototype, 'warn').mockImplementation(() => {});
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('sends the engine the current topology, in its format', async () => {
    const engine = new FakeEngine(async (request) => echoing(request));
    const service = new AnalysisService(topology, engine);

    const result = await service.analyze(ENVIRONMENT_ID, {
      analyses: ['blast_radius', 'attack_paths'],
      seedNodeIds: ['b', 'a'],
      maxDepth: 3,
    });

    expect(engine.calls).toEqual([
      {
        topology: toEngineTopology(snapshotAt(1)),
        analyses: ['attack_paths', 'blast_radius'],
        options: { seedNodeIds: ['a', 'b'], maxDepth: 3 },
      },
    ]);
    expect(result).toEqual({
      environmentId: ENVIRONMENT_ID,
      topologyRevision: 'epoch-a:1',
      analyzedAt: '2026-11-02T10:00:01Z',
      freshness: 'current',
      results: { criticalNodes: { nodes: [] } },
    });
  });

  it.each([
    ['a newer revision', () => snapshotAt(2)],
    ['no topology any more', () => null],
  ])('flags the results as stale when there is %s', async (_name, next) => {
    const engine = new FakeEngine(async (request) => {
      // The topology changes while the engine works.
      current = next();

      return echoing(request);
    });

    const result = await new AnalysisService(topology, engine).analyze(
      ENVIRONMENT_ID,
      ask,
    );

    expect(result.freshness).toBe('stale');
    expect(result.topologyRevision).toBe('epoch-a:1');
  });

  it('flags the results as unverifiable when the revision is not echoed', async () => {
    const engine = new FakeEngine(async (request) => {
      const { topologyRevision: _revision, ...data } = echoing(request);

      return data;
    });

    const result = await new AnalysisService(topology, engine).analyze(
      ENVIRONMENT_ID,
      ask,
    );

    expect(result.freshness).toBe('unverifiable');
    expect(result.results).toEqual({ criticalNodes: { nodes: [] } });
  });

  it('asks the engine once for the same analysis at the same time', async () => {
    let release!: () => void;
    const released = new Promise<void>((resolve) => (release = resolve));
    const engine = new FakeEngine(async (request) => {
      await released;

      return echoing(request);
    });
    const service = new AnalysisService(topology, engine);

    // The seeds come in another order: it is still the same analysis.
    const first = service.analyze(ENVIRONMENT_ID, {
      analyses: ['blast_radius'],
      seedNodeIds: ['a', 'b'],
    });
    const second = service.analyze(ENVIRONMENT_ID, {
      analyses: ['blast_radius'],
      seedNodeIds: ['b', 'a'],
    });

    release();

    expect(await first).toEqual(await second);
    expect(engine.calls).toHaveLength(1);
  });

  it('asks the engine again for another analysis, and after the first one', async () => {
    const engine = new FakeEngine(async (request) => echoing(request));
    const service = new AnalysisService(topology, engine);

    await Promise.all([
      service.analyze(ENVIRONMENT_ID, {
        analyses: ['blast_radius'],
        seedNodeIds: ['a'],
      }),
      service.analyze(ENVIRONMENT_ID, {
        analyses: ['blast_radius'],
        seedNodeIds: ['b'],
      }),
    ]);
    await service.analyze(ENVIRONMENT_ID, {
      analyses: ['blast_radius'],
      seedNodeIds: ['a'],
    });

    expect(engine.calls).toHaveLength(3);
  });

  it('asks again for the new revision of the topology', async () => {
    const engine = new FakeEngine(async (request) => echoing(request));
    const service = new AnalysisService(topology, engine);

    const before = service.analyze(ENVIRONMENT_ID, ask);

    current = snapshotAt(2);

    const after = service.analyze(ENVIRONMENT_ID, ask);

    expect((await before).topologyRevision).toBe('epoch-a:1');
    expect((await after).topologyRevision).toBe('epoch-a:2');
    expect(engine.calls).toHaveLength(2);
  });

  it('forgets a call that failed', async () => {
    let failing = true;
    const engine = new FakeEngine(async (request) => {
      if (failing) {
        throw new Error('fetch failed');
      }

      return echoing(request);
    });
    const service = new AnalysisService(topology, engine);

    await expect(service.analyze(ENVIRONMENT_ID, ask)).rejects.toThrow();

    failing = false;

    expect((await service.analyze(ENVIRONMENT_ID, ask)).freshness).toBe(
      'current',
    );
    expect(engine.calls).toHaveLength(2);
  });

  describe('errors', () => {
    const failWith = async (answer: () => Promise<AnalyzeData>) =>
      new AnalysisService(topology, new FakeEngine(answer))
        .analyze(ENVIRONMENT_ID, ask)
        .catch((error: unknown) => error);

    it('says the analyses are unavailable when the engine cannot be used', async () => {
      const failure = await failWith(async () => {
        throw new Error('fetch failed');
      });

      expect(failure).toBeInstanceOf(ServiceUnavailableException);
      expect(failure).toMatchObject({ errorCode: 'ANALYSIS_UNAVAILABLE' });
    });

    it('says the same when no engine is configured', async () => {
      const failure = await new AnalysisService(topology, null)
        .analyze(ENVIRONMENT_ID, ask)
        .catch((error: unknown) => error);

      expect(failure).toBeInstanceOf(ServiceUnavailableException);
      expect(failure).toMatchObject({ errorCode: 'ANALYSIS_UNAVAILABLE' });
    });

    it('keeps the code and the message of a request the engine refuses', async () => {
      const failure = await failWith(async () => {
        throw new GraphEngineError(
          400,
          'GRAPH_MISSING_SEEDS',
          'Analysis blast_radius requires options.seedNodeIds',
        );
      });

      expect(failure).toBeInstanceOf(BadRequestException);
      expect(failure).toMatchObject({
        errorCode: 'GRAPH_MISSING_SEEDS',
        message: 'Analysis blast_radius requires options.seedNodeIds',
      });
    });

    it('answers a bad gateway when the engine itself fails', async () => {
      const failure = await failWith(async () => {
        throw new GraphEngineError(
          500,
          'GRAPH_ENGINE_FAILURE',
          'secret detail',
        );
      });

      expect(failure).toBeInstanceOf(BadGatewayException);
      expect(failure).toMatchObject({ errorCode: 'GRAPH_ENGINE_FAILURE' });
      expect((failure as Error).message).not.toContain('secret detail');
    });

    it('does not call the engine without a topology', async () => {
      current = null;

      const engine = new FakeEngine(async (request) => echoing(request));
      const failure = await new AnalysisService(topology, engine)
        .analyze(ENVIRONMENT_ID, ask)
        .catch((error: unknown) => error);

      expect(failure).toBeInstanceOf(ServiceUnavailableException);
      expect(failure).toMatchObject({ errorCode: 'TOPOLOGY_UNAVAILABLE' });
      expect(engine.calls).toHaveLength(0);
    });

    it('does not call the engine for an unknown environment', async () => {
      const engine = new FakeEngine(async (request) => echoing(request));
      const failure = await new AnalysisService(topology, engine)
        .analyze('env_other', ask)
        .catch((error: unknown) => error);

      expect(failure).toBeInstanceOf(NotFoundException);
      expect(failure).toMatchObject({
        errorCode: 'TOPOLOGY_UNKNOWN_ENVIRONMENT',
      });
      expect(engine.calls).toHaveLength(0);
    });
  });
});
