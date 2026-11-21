
import type { CollectorSnapshot } from './types.js';

const DEMO_PROJECT = 'ft-demo-lab';
const DEMO_SERVICES = ['db', 'api', 'web', 'worker'] as const;

export function renderDemoLabMetrics(
  snapshot: CollectorSnapshot,
): string {
  const present: string[] = [];
  const running: string[] = [];
  const healthy: string[] = [];

  for (const service of DEMO_SERVICES) {
    const containers = snapshot.containers.filter(
      (container) =>
        container.compose?.project === DEMO_PROJECT &&
        container.compose.service === service,
    );

    const exists = containers.length > 0;
    const isRunning = containers.some(
      (container) => container.state === 'running',
    );
    const isHealthy = containers.some(
      (container) =>
        container.state === 'running' &&
        container.health === 'healthy',
    );

    const label = `{service="${service}"}`;

    present.push(
      `ft_demo_service_present${label} ${Number(exists)}`,
    );
    running.push(
      `ft_demo_service_running${label} ${Number(isRunning)}`,
    );
    healthy.push(
      `ft_demo_service_healthy${label} ${Number(isHealthy)}`,
    );
  }

  const labPresent = snapshot.containers.some(
    (container) =>
      container.compose?.project === DEMO_PROJECT &&
      DEMO_SERVICES.some(
        (service) => service === container.compose?.service,
      ),
  );

  return [
    '# HELP ft_demo_lab_present Whether Demo Lab containers exist.',
    '# TYPE ft_demo_lab_present gauge',
    `ft_demo_lab_present ${Number(labPresent)}`,
    '# HELP ft_demo_service_present Whether the service container exists.',
    '# TYPE ft_demo_service_present gauge',
    ...present,
    '# HELP ft_demo_service_running Whether the service is running.',
    '# TYPE ft_demo_service_running gauge',
    ...running,
    '# HELP ft_demo_service_healthy Whether the running service is healthy.',
    '# TYPE ft_demo_service_healthy gauge',
    ...healthy,
    '',
  ].join('\n');
}
