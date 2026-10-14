import type {
  ContainerHealth,
  ContainerState,
} from '../../src/modules/topology/collector.types.js';
import type { NodeStatus } from '../../src/modules/topology/graph.types.js';
import {
  bestStatus,
  containerStatus,
  edgeId,
  serviceId,
} from '../../src/modules/topology/mapping.js';

describe('topology ids', () => {
  it('keeps two different projects and services apart', () => {
    expect(serviceId('a_b', 'c')).not.toBe(serviceId('a', 'b_c'));
  });

  it('builds an edge id from its kind and both ends', () => {
    expect(edgeId('depends_on', 'svc:demo:api', 'svc:demo:db')).toBe(
      'depends_on:svc:demo:api>svc:demo:db',
    );
  });
});

describe('containerStatus', () => {
  const cases: [ContainerState, ContainerHealth, NodeStatus][] = [
    ['running', 'none', 'healthy'],
    ['running', 'healthy', 'healthy'],
    ['running', 'unhealthy', 'degraded'],
    ['running', 'starting', 'unknown'],
    ['restarting', 'none', 'degraded'],
    ['created', 'none', 'unknown'],
    ['exited', 'none', 'down'],
    ['dead', 'none', 'down'],
    ['paused', 'none', 'down'],
    ['removing', 'none', 'down'],
    // A stopped container was last seen unhealthy: still just down.
    ['exited', 'unhealthy', 'down'],
  ];

  it.each(cases)('%s + %s -> %s', (state, health, expected) => {
    expect(containerStatus({ state, health })).toBe(expected);
  });

  it('answers unknown for a state this version does not know', () => {
    const state = 'migrating' as ContainerState;

    expect(containerStatus({ state, health: 'none' })).toBe('unknown');
  });

  it('never produces compromised', () => {
    const states: ContainerState[] = [
      'created',
      'running',
      'paused',
      'restarting',
      'removing',
      'exited',
      'dead',
    ];
    const healths: ContainerHealth[] = [
      'healthy',
      'unhealthy',
      'starting',
      'none',
    ];

    for (const state of states) {
      for (const health of healths) {
        expect(containerStatus({ state, health })).not.toBe('compromised');
      }
    }
  });
});

describe('bestStatus', () => {
  it('prefers a running instance to a stopped one', () => {
    expect(bestStatus(['down', 'healthy'])).toBe('healthy');
  });

  it('prefers a starting instance to a stopped one', () => {
    expect(bestStatus(['down', 'unknown'])).toBe('unknown');
  });

  it('keeps the only status', () => {
    expect(bestStatus(['degraded'])).toBe('degraded');
  });
});
