import {
  describe,
  expect,
  it,
} from 'vitest';

import {
  normalizeContainer,
  normalizeNetwork,
  normalizeVolume,
} from '../src/normalize.js';

import type {
  DockerContainerInspect,
  DockerNetwork,
  DockerVolume,
} from '../src/docker-client.js';

function makeContainer(): DockerContainerInspect {
  return {
    Id: 'container-1',
    Name: '/collector',
    Config: {
      Image: 'collector:latest',
      Labels: null,
    },
    State: {
      Status: 'running',
    },
    NetworkSettings: {
      Ports: null,
      Networks: null,
    },
    Mounts: [],
  } as unknown as DockerContainerInspect;
}

describe('normalizeContainer', () => {
  it('normalizes basic container fields', () => {
    const result = normalizeContainer(
      makeContainer(),
    );

    expect(result).toMatchObject({
      id: 'container-1',
      name: 'collector',
      image: 'collector:latest',
      state: 'running',
      health: 'none',
      labels: {},
      compose: null,
      ports: [],
      networks: [],
      mounts: [],
    });
  });

  it('normalizes container health', () => {
    const container = makeContainer();

    container.State.Health = {
      Status: 'healthy',
    };

    const result =
      normalizeContainer(container);

    expect(result.health).toBe('healthy');
  });

  it('extracts Compose metadata and removes Compose labels from generic labels', () => {
    const container = makeContainer();

    container.Config.Labels = {
      'com.docker.compose.project':
        'transcendence',
      'com.docker.compose.service':
        'collector',
      'com.docker.compose.depends_on':
        'docker-proxy:service_started:false,postgres:service_healthy:false',
      'example.custom-label': 'visible',
    };

    const result =
      normalizeContainer(container);

    expect(result.compose).toEqual({
      project: 'transcendence',
      service: 'collector',
      dependsOn: [
        'docker-proxy',
        'postgres',
      ],
    });

    expect(result.labels).toEqual({
      'example.custom-label': 'visible',
    });

    expect(
      Object.keys(result.labels).some(
        (key) =>
          key.startsWith(
            'com.docker.compose.',
          ),
      ),
    ).toBe(false);
  });

  it('redacts bind-mount host paths but preserves volume names', () => {
    const container = makeContainer();

    container.Mounts = [
      {
        Type: 'bind',
        Source:
          '/home/user/private/project',
        Destination: '/app/data',
        RW: true,
      },
      {
        Type: 'volume',
        Name: 'postgres_data',
        Source:
          '/var/lib/docker/volumes/postgres_data/_data',
        Destination:
          '/var/lib/postgresql/data',
        RW: false,
      },
    ];

    const result =
      normalizeContainer(container);

    expect(result.mounts).toEqual([
      {
        type: 'bind',
        source: '[redacted]',
        destination: '/app/data',
        readOnly: false,
      },
      {
        type: 'volume',
        source: 'postgres_data',
        destination:
          '/var/lib/postgresql/data',
        readOnly: true,
      },
    ]);
  });

  it('normalizes published ports', () => {
    const container = makeContainer();

    container.NetworkSettings.Ports = {
      '3001/tcp': [
        {
          HostIp: '127.0.0.1',
          HostPort: '3001',
        },
      ],
      '8080/tcp': [
        {
          HostIp: '',
          HostPort: '8080',
        },
      ],
      '9000/tcp': null,
    };

    const result =
      normalizeContainer(container);

    expect(result.ports).toEqual([
      {
        containerPort: 3001,
        protocol: 'tcp',
        hostIp: '127.0.0.1',
        hostPort: 3001,
      },
      {
        containerPort: 8080,
        protocol: 'tcp',
        hostIp: '0.0.0.0',
        hostPort: 8080,
      },
    ]);
  });

  it('normalizes attached networks', () => {
    const container = makeContainer();

    container.NetworkSettings.Networks = {
      transcendence_default: {
        NetworkID: 'network-1',
        IPAddress: '172.19.0.3',
      },
      bridge: {
        NetworkID: 'network-2',
        IPAddress: '',
      },
    };

    const result =
      normalizeContainer(container);

    expect(result.networks).toEqual([
      {
        networkId: 'network-1',
        name: 'transcendence_default',
        ipv4Address: '172.19.0.3',
      },
      {
        networkId: 'network-2',
        name: 'bridge',
        ipv4Address: '',
      },
    ]);
  });

  it('keeps distinct port bindings with different host IPs', () => {
    const container = makeContainer();

    container.NetworkSettings.Ports = {
        '3001/tcp': [
        {
            HostIp: '127.0.0.1',
            HostPort: '3001',
        },
        {
            HostIp: '0.0.0.0',
            HostPort: '3001',
        },
        ],
    };

    const result =
        normalizeContainer(container);

    expect(result.ports).toEqual([
        {
        containerPort: 3001,
        protocol: 'tcp',
        hostIp: '127.0.0.1',
        hostPort: 3001,
        },
        {
        containerPort: 3001,
        protocol: 'tcp',
        hostIp: '0.0.0.0',
        hostPort: 3001,
        },
    ]);
    });
});

describe('normalizeNetwork', () => {
  it('normalizes a Docker network', () => {
    const network = {
      Id: 'network-1',
      Name: 'transcendence_default',
      Driver: 'bridge',
      Internal: false,
      Labels: {
        'example.label': 'value',
        'com.docker.compose.project':
          'transcendence',
      },
    } as DockerNetwork;

    expect(
      normalizeNetwork(network),
    ).toEqual({
      id: 'network-1',
      name: 'transcendence_default',
      driver: 'bridge',
      internal: false,
      labels: {
        'example.label': 'value',
      },
    });
  });
});

describe('normalizeVolume', () => {
  it('normalizes a Docker volume', () => {
    const volume = {
      Name: 'postgres_data',
      Driver: 'local',
      Labels: {
        'example.label': 'value',
        'com.docker.compose.project':
          'transcendence',
      },
    } as DockerVolume;

    expect(
      normalizeVolume(volume),
    ).toEqual({
      name: 'postgres_data',
      driver: 'local',
      labels: {
        'example.label': 'value',
      },
    });
  });
});