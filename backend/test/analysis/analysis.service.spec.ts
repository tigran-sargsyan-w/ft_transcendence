import type { MockInstance } from 'vitest';
import {
  BadGatewayException,
  BadRequestException,
  Logger,
  NotFoundException,
  ServiceUnavailableException,
} from '@nestjs/common';
import { RESULT_KEYS } from '../../src/modules/analysis/analyses.js';
import {
  type AnalysisRequest,
  AnalysisService,
} from '../../src/modules/analysis/analysis.service.js';
import {
  type AnalyzeData,
  type AnalyzeRequest,
  type GraphEngineClient,
  GraphEngineError,
} from '../../src/modules/analysis/graph-engine-client.js';
import { buildGraph } from '../../src/modules/topology/build-graph.js';
import { toEngineTopology } from '../../src/modules/topology/engine-topology.js';
import type { TopologySnapshot } from '../../src/modules/topology/graph.types.js';
import type { TopologyStatus } from '../../src/modules/topology/topology.service.js';
import { ENVIRONMENT_ID, api, db, web } from '../topology/fixtures.js';

const graph = buildGraph(ENVIRONMENT_ID, [db(), api(), web()]);

// Nodes of that topology.
const [DB, API, WEB] = ['db', 'api', 'web'].map((name) => `svc:demo:${name}`);

const snapshotAt = (revision: number): TopologySnapshot => ({
  schemaVersion: 1,
  environmentId: ENVIRONMENT_ID,
  capturedAt: '2026-11-02T10:00:00.000Z',
  epoch: 'epoch-a',
  revision,
  ...graph,
});

// A result for each analysis that was asked for, as the engine does.
const resultsFor = (analyses: string[]) =>
  Object.fromEntries(
    analyses.map((id) => [
      RESULT_KEYS[id as keyof typeof RESULT_KEYS],
      { analysis: id },
    ]),
  );

// What a well behaved engine answers: the revision it was sent, echoed.
const echoing = (request: AnalyzeRequest): AnalyzeData => ({
  schemaVersion: 1,
  environmentId: request.topology.environmentId,
  topologyRevision: request.topology.topologyRevision,
  analyzedAt: '2026-11-02T10:00:01Z',
  results: resultsFor(request.analyses),
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
  let warn: MockInstance<Logger['warn']>;
  let collector: TopologyStatus;
  const topology = { snapshot: () => current, status: () => collector };
  const ask = { analyses: ['critical_nodes'] };

  beforeEach(() => {
    current = snapshotAt(1);
    collector = { stale: false, since: null };
    warn = vi.spyOn(Logger.prototype, 'warn').mockImplementation(() => {});
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('sends the engine the current topology, in its format', async () => {
    const engine = new FakeEngine(async (request) => echoing(request));
    const service = new AnalysisService(topology, engine);

    const result = await service.analyze(ENVIRONMENT_ID, {
      analyses: ['blast_radius', 'attack_paths'],
      seedNodeIds: [WEB, API],
      maxDepth: 3,
    });

    expect(engine.calls).toEqual([
      {
        topology: toEngineTopology(snapshotAt(1)),
        analyses: ['attack_paths', 'blast_radius'],
        options: { seedNodeIds: [API, WEB], maxDepth: 3 },
      },
    ]);
    expect(result).toEqual({
      environmentId: ENVIRONMENT_ID,
      topologyRevision: 'epoch-a:1',
      analyzedAt: '2026-11-02T10:00:01Z',
      freshness: 'current',
      results: {
        attackPaths: { analysis: 'attack_paths' },
        blastRadius: { analysis: 'blast_radius' },
      },
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

  it.each([
    ['is unreachable when the analysis starts', true],
    ['becomes unreachable while the engine works', false],
  ])(
    'flags the results as stale when the collector %s',
    async (_name, downFirst) => {
      const down = { stale: true, since: '2026-11-02T10:00:00Z' };

      if (downFirst) {
        collector = down;
      }

      const engine = new FakeEngine(async (request) => {
        if (!downFirst) {
          collector = down;
        }

        return echoing(request);
      });

      // The revision never changes: only the collector is down.
      const result = await new AnalysisService(topology, engine).analyze(
        ENVIRONMENT_ID,
        ask,
      );

      expect(result.topologyRevision).toBe('epoch-a:1');
      expect(result.freshness).toBe('stale');
    },
  );

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
    expect(result.results).toEqual({
      criticalNodes: { analysis: 'critical_nodes' },
    });
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
      seedNodeIds: [API, WEB],
    });
    const second = service.analyze(ENVIRONMENT_ID, {
      analyses: ['blast_radius'],
      seedNodeIds: [WEB, API],
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
        seedNodeIds: [API],
      }),
      service.analyze(ENVIRONMENT_ID, {
        analyses: ['blast_radius'],
        seedNodeIds: [WEB],
      }),
    ]);
    await service.analyze(ENVIRONMENT_ID, {
      analyses: ['blast_radius'],
      seedNodeIds: [API],
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
    const failWith = async (
      answer: (sent: AnalyzeRequest) => Promise<AnalyzeData>,
    ) =>
      new AnalysisService(topology, new FakeEngine(answer))
        .analyze(ENVIRONMENT_ID, ask)
        .catch((error: unknown) => error);

    it('says the analyses are unavailable when the engine cannot be used', async () => {
      const failure = await failWith(async () => {
        throw new Error('fetch failed');
      });

      expect(failure).toBeInstanceOf(ServiceUnavailableException);
      expect(failure).toMatchObject({ errorCode: 'ANALYSIS_UNAVAILABLE' });
      expect(warn).toHaveBeenCalledWith(
        expect.stringContaining('fetch failed'),
      );
    });

    it('says the same when no engine is configured', async () => {
      const failure = await new AnalysisService(topology, null)
        .analyze(ENVIRONMENT_ID, ask)
        .catch((error: unknown) => error);

      expect(failure).toBeInstanceOf(ServiceUnavailableException);
      expect(failure).toMatchObject({ errorCode: 'ANALYSIS_UNAVAILABLE' });
    });

    it('answers a bad gateway when the engine refuses a request that was valid', async () => {
      const failure = await failWith(async () => {
        throw new GraphEngineError(
          400,
          'GRAPH_INVALID_TOPOLOGY',
          'Unknown node kind: widget',
        );
      });

      expect(failure).toBeInstanceOf(BadGatewayException);
      expect(failure).toMatchObject({ errorCode: 'GRAPH_ENGINE_FAILURE' });
      expect((failure as Error).message).not.toContain('widget');
      // Not the user's doing: the log has the engine's own words.
      expect(warn).toHaveBeenCalledWith(
        expect.stringContaining(
          '400 GRAPH_INVALID_TOPOLOGY: Unknown node kind: widget',
        ),
      );
    });

    const withoutEngineCall = async (request: AnalysisRequest) => {
      const engine = new FakeEngine(async (sent) => echoing(sent));
      const failure = await new AnalysisService(topology, engine)
        .analyze(ENVIRONMENT_ID, request)
        .catch((error: unknown) => error);

      expect(failure).toBeInstanceOf(BadRequestException);
      expect(failure).toMatchObject({ errorCode: 'VALIDATION_ERROR' });
      expect(engine.calls).toHaveLength(0);

      return (failure as BadRequestException).getResponse();
    };

    it.each([
      ['blast_radius', undefined],
      ['attack_paths', []],
    ])(
      'refuses %s without seeds, before calling the engine',
      async (analysis, seedNodeIds) => {
        expect(
          await withoutEngineCall({ analyses: [analysis], seedNodeIds }),
        ).toMatchObject({
          details: {
            seedNodeIds: [
              'seedNodeIds is required for blast_radius and attack_paths',
            ],
          },
        });
      },
    );

    it.each([['blast_radius'], ['critical_nodes']])(
      'refuses seeds that are not nodes of the topology (%s), before calling the engine',
      async (analysis) => {
        // The engine refuses them whatever the analysis.
        expect(
          await withoutEngineCall({
            analyses: [analysis],
            seedNodeIds: ['x', API, 'svc:demo:nope'],
          }),
        ).toMatchObject({
          details: {
            seedNodeIds: [
              'svc:demo:nope is not a node of the topology',
              'x is not a node of the topology',
            ],
          },
        });
      },
    );

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
      // The detail goes to the log, not to the client.
      expect(warn).toHaveBeenCalledWith(
        expect.stringContaining('secret detail'),
      );
    });

    it.each([
      ['another revision than the one sent', { topologyRevision: 'epoch-a:9' }],
      ['another environment', { environmentId: 'env_other' }],
    ])('rejects an answer about %s', async (_name, other) => {
      const failure = await failWith(async (sent) => ({
        ...echoing(sent),
        ...other,
      }));

      expect(failure).toBeInstanceOf(ServiceUnavailableException);
      expect(failure).toMatchObject({ errorCode: 'ANALYSIS_UNAVAILABLE' });
      expect(warn).toHaveBeenCalledWith(
        expect.stringContaining('answered about'),
      );
    });

    it('rejects the current revision when it is not the one that was sent', async () => {
      // The topology moves on while the engine works, and the engine answers
      // with the new revision: that says nothing about the topology it got.
      const failure = await failWith(async (sent) => {
        current = snapshotAt(2);

        return { ...echoing(sent), topologyRevision: 'epoch-a:2' };
      });

      expect(failure).toBeInstanceOf(ServiceUnavailableException);
      expect(failure).toMatchObject({ errorCode: 'ANALYSIS_UNAVAILABLE' });
    });

    it.each([
      ['no result at all', {}],
      ['a null result', { criticalNodes: null }],
      ['a result of another analysis', { blastRadius: {} }],
    ])(
      'rejects an answer with %s for the analysis asked',
      async (_name, results) => {
        const failure = await failWith(async (sent) => ({
          ...echoing(sent),
          results,
        }));

        expect(failure).toBeInstanceOf(ServiceUnavailableException);
        expect(failure).toMatchObject({ errorCode: 'ANALYSIS_UNAVAILABLE' });
        expect(warn).toHaveBeenCalledWith(
          expect.stringContaining('no result for critical_nodes'),
        );
      },
    );

    it('rejects an answer that leaves out one of the analyses asked', async () => {
      const engine = new FakeEngine(async (sent) => ({
        ...echoing(sent),
        results: { blastRadius: {} },
      }));
      const failure = await new AnalysisService(topology, engine)
        .analyze(ENVIRONMENT_ID, {
          analyses: ['blast_radius', 'critical_nodes'],
          seedNodeIds: [DB],
        })
        .catch((error: unknown) => error);

      expect(failure).toBeInstanceOf(ServiceUnavailableException);
      expect(warn).toHaveBeenCalledWith(
        expect.stringContaining('no result for critical_nodes'),
      );
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
