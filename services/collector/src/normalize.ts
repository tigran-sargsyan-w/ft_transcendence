import type {
  CollectorContainer,
  CollectorNetwork,
  CollectorVolume,
  ContainerHealth,
  ContainerState,
  DockerLabels,
} from './types.js';

import type {
  DockerContainerInspect,
  DockerNetwork,
  DockerVolume,
} from './docker-client.js';

const COMPOSE_PREFIX = 'com.docker.compose.';

function normalizeLabels(
  labels: Record<string, string> | null,
): DockerLabels {
  if (labels === null) {
    return {};
  }

  return Object.fromEntries(
    Object.entries(labels).filter(
      ([key]) => !key.startsWith(COMPOSE_PREFIX),
    ),
  );
}

function normalizeDependsOn(value: string | undefined): string[] {
  if (!value) {
    return [];
  }

  return value
    .split(',')
    .map((dependency) => dependency.split(':')[0])
    .filter((dependency) => dependency.length > 0);
}

function normalizeCompose(
  labels: Record<string, string> | null,
): CollectorContainer['compose'] {
  if (labels === null) {
    return null;
  }

  const project = labels['com.docker.compose.project'];
  const service = labels['com.docker.compose.service'];

  if (!project || !service) {
    return null;
  }

  return {
    project,
    service,
    dependsOn: normalizeDependsOn(
      labels['com.docker.compose.depends_on'],
    ),
  };
}

function normalizePorts(
  inspect: DockerContainerInspect,
): CollectorContainer['ports'] {
  const ports = inspect.NetworkSettings.Ports;

  if (ports === null) {
    return [];
  }

  const result: CollectorContainer['ports'] = [];

  for (const [containerBinding, hostBindings] of Object.entries(ports)) {
    if (hostBindings === null) {
      continue;
    }

    const [portValue, protocol] = containerBinding.split('/');
    const containerPort = Number(portValue);

    for (const binding of hostBindings) {
      const hostPort = Number(binding.HostPort);

      const hostIp =
        binding.HostIp || '0.0.0.0';

      const alreadyPresent = result.some(
        (entry) =>
          entry.containerPort === containerPort &&
          entry.protocol === protocol &&
          entry.hostIp === hostIp &&
          entry.hostPort === hostPort,
      );

      if (alreadyPresent) {
        continue;
      }

      result.push({
        containerPort,
        protocol,
        hostIp,
        hostPort,
      });
    }
  }

  return result;
}

function normalizeNetworks(
  inspect: DockerContainerInspect,
): CollectorContainer['networks'] {
  const networks = inspect.NetworkSettings.Networks;

  if (networks === null) {
    return [];
  }

  return Object.entries(networks).map(([name, network]) => ({
    networkId: network.NetworkID ?? '',
    name,
    ipv4Address: network.IPAddress ?? '',
  }));
}

function normalizeMounts(
  inspect: DockerContainerInspect,
): CollectorContainer['mounts'] {
  return inspect.Mounts.map((mount) => {
    let source = mount.Source;

    if (mount.Type === 'volume') {
      source = mount.Name ?? mount.Source;
    }

    if (mount.Type === 'bind') {
      source = '[redacted]';
    }

    return {
      type: mount.Type,
      source,
      destination: mount.Destination,
      readOnly: !mount.RW,
    };
  });
}

export function normalizeContainer(
  inspect: DockerContainerInspect,
): CollectorContainer {
  const labels = inspect.Config.Labels;

  return {
    id: inspect.Id,
    name: inspect.Name.replace(/^\//, ''),
    image: inspect.Config.Image,
    state: inspect.State.Status as ContainerState,
    health:
      (inspect.State.Health?.Status as ContainerHealth | undefined) ??
      'none',
    labels: normalizeLabels(labels),
    compose: normalizeCompose(labels),
    ports: normalizePorts(inspect),
    networks: normalizeNetworks(inspect),
    mounts: normalizeMounts(inspect),
  };
}

export function normalizeNetwork(
  network: DockerNetwork,
): CollectorNetwork {
  return {
    id: network.Id,
    name: network.Name,
    driver: network.Driver,
    internal: network.Internal,
    labels: normalizeLabels(network.Labels),
  };
}

export function normalizeVolume(
  volume: DockerVolume,
): CollectorVolume {
  return {
    name: volume.Name,
    driver: volume.Driver,
    labels: normalizeLabels(volume.Labels),
  };
}