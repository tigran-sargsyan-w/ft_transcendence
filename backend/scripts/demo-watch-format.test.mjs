import test from 'node:test';
import assert from 'node:assert/strict';
import { formatAnalysisSummary, formatLabDependencyChange, shortLabId } from './demo-watch-format.mjs';

const id = (name) => `svc:ft-demo-lab:${name}`;

test('summarizes a real-world three-level DB cascade and hides unrelated infrastructure', () => {
  const lines = formatAnalysisSummary({
    seedId: id('db'),
    freshness: 'current',
    topologyRevision: 'epoch:74',
    results: {
      blastRadius: {
        affectedNodeIds: [id('web'), id('db'), id('api')],
        depthByNodeId: { [id('web')]: 2, [id('db')]: 0, [id('api')]: 1 },
      },
      criticalNodes: {
        nodes: [
          { nodeId: id('web'), score: 0 },
          { nodeId: 'svc:transcendence-vault-dev:vault', score: 0.99 },
          { nodeId: id('api'), score: 0.25 },
          { nodeId: id('db'), score: 0.4 },
        ],
      },
    },
  });
  assert.deepEqual(lines, [
    'ANALYSIS seed=db freshness=current revision=epoch:74',
    '  BLAST potential impact: db(depth 0), api(depth 1), web(depth 2)',
    '  CRITICAL lab scores: db=0.40, api=0.25, web=0.00',
  ]);
});

test('reports potentially affected nodes outside the lab without silently hiding them', () => {
  const lines = formatAnalysisSummary({
    seedId: id('api'),
    freshness: 'stale',
    topologyRevision: 'epoch:3',
    results: {
      blastRadius: {
        affectedNodeIds: [id('api'), 'svc:other:worker'],
        depthByNodeId: { [id('api')]: 0 },
      },
      criticalNodes: { nodes: [] },
    },
  });
  assert.match(lines[0], /freshness=stale/);
  assert.equal(lines[1], '  BLAST potential impact: api(depth 0); 1 outside lab');
  assert.equal(lines[2], '  CRITICAL lab scores: none');
});

test('handles omitted analysis sections and missing depths', () => {
  assert.deepEqual(formatAnalysisSummary({ seedId: id('web'), results: {} }), [
    'ANALYSIS seed=web freshness=unknown revision=unknown',
    '  BLAST unavailable',
    '  CRITICAL unavailable',
  ]);
  const lines = formatAnalysisSummary({
    seedId: id('db'),
    results: {
      blastRadius: { affectedNodeIds: [id('db')] },
      criticalNodes: { nodes: [{ nodeId: id('db'), score: NaN }] },
    },
  });
  assert.equal(lines[1], '  BLAST potential impact: db(depth ?)');
  assert.equal(lines[2], '  CRITICAL lab scores: none');
});

test('shortens only lab service ids', () => {
  assert.equal(shortLabId(id('web')), 'web');
  assert.equal(shortLabId('svc:other:web'), 'svc:other:web');
});


test('reports lab-only dynamic dependency changes with short names', () => {
  const edge = { kind: 'depends_on', source: id('worker'), target: id('api') };
  assert.equal(formatLabDependencyChange(edge, 'added'), 'DEPENDENCY added: worker -> api');
  assert.equal(formatLabDependencyChange(edge, 'removed'), 'DEPENDENCY removed: worker -> api');
  assert.equal(formatLabDependencyChange(edge, 'other'), null);
});

test('ignores other projects and non-dependency edges', () => {
  assert.equal(formatLabDependencyChange({ kind: 'connects_to', source: id('worker'), target: 'net:x' }, 'added'), null);
  assert.equal(formatLabDependencyChange({ kind: 'depends_on', source: 'svc:app:worker', target: id('api') }, 'added'), null);
  assert.equal(formatLabDependencyChange(null, 'added'), null);
});
