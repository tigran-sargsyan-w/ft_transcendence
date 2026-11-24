
import { describe, expect, it } from 'vitest';

import { renderDemoLabMetrics } from '../src/metrics.js';
import type {
  CollectorContainer,
  CollectorSnapshot,
  ContainerHealth,
  ContainerState,
} from '../src/types.js';

function makeContainer(
  service: string,
  state: ContainerState,
  health: ContainerHealth,
  project = 'ft-demo-lab',
): CollectorContainer {
  return {
    id: `${project}-${service}`,
    name: `${project}-${service}-1`,
    image: 'demo-image',
    state,
    health,
    labels: { secret: 'must-not-be-exported' },
    compose: {
      project,
      service,
      dependsOn: [],
    },
    ports: [],
    networks: [],
    mounts: [],
  };
}

function makeSnapshot(
  containers: CollectorContainer[],
): CollectorSnapshot {
  return {
    schemaVersion: 1,
    streamId: 'test-stream',
    environmentId: 'test-environment',
    capturedAt: '2026-11-21T12:00:00.000Z',
    sequence: 0,
    containers,
    networks: [],
    volumes: [],
  };
}

describe('Demo Lab Prometheus metrics', () => {
  it('reports healthy services', () => {
    const output = renderDemoLabMetrics(
      makeSnapshot([
        makeContainer('db', 'running', 'healthy'),
      ]),
    );

    expect(output).toContain('ft_demo_lab_present 1');
    expect(output).toContain(
      'ft_demo_service_running{service="db"} 1',
    );
    expect(output).toContain(
      'ft_demo_service_healthy{service="db"} 1',
    );
  });

  it('distinguishes unhealthy from stopped', () => {
    const output = renderDemoLabMetrics(
      makeSnapshot([
        makeContainer('db', 'running', 'unhealthy'),
        makeContainer('api', 'exited', 'unhealthy'),
      ]),
    );

    expect(output).toContain(
      'ft_demo_service_running{service="db"} 1',
    );
    expect(output).toContain(
      'ft_demo_service_healthy{service="db"} 0',
    );
    expect(output).toContain(
      'ft_demo_service_running{service="api"} 0',
    );
  });

  it('handles an absent Demo Lab', () => {
    const output = renderDemoLabMetrics(
      makeSnapshot([]),
    );

    expect(output).toContain('ft_demo_lab_present 0');
    expect(output).toContain(
      'ft_demo_service_present{service="worker"} 0',
    );
  });

  it('ignores unrelated containers and sensitive labels', () => {
    const output = renderDemoLabMetrics(
      makeSnapshot([
        makeContainer('db', 'running', 'healthy', 'other-project'),
        makeContainer('unknown', 'running', 'healthy'),
      ]),
    );

    expect(output).toContain('ft_demo_lab_present 0');
    expect(output).not.toContain('unknown');
    expect(output).not.toContain('must-not-be-exported');
    expect(output).not.toContain('other-project');
  });
});
