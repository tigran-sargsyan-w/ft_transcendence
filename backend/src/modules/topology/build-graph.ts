import type { CollectorContainer } from './collector.types.js';
import type { Graph, GraphEdge, GraphNode } from './graph.types.js';
import {
  EXTERNAL_NODE_ID,
  bestStatus,
  containerStatus,
  edgeId,
  hostNodeId,
  networkNodeId,
  serviceId,
  standaloneId,
  statusRank,
  volumeNodeId,
} from './mapping.js';

type Service = {
  id: string;
  label: string;
  // Instances of one service: several while Compose recreates a container.
  instances: CollectorContainer[];
};

type PublishedPort = {
  containerPort: number;
  protocol: string;
  hostPort: number;
  // Bound to 127.0.0.1 or ::1 only: not reachable from outside the host.
  loopbackOnly: boolean;
};

const isLoopback = (hostIp: string) =>
  hostIp === '::1' || hostIp.startsWith('127.');

const byId = (a: { id: string }, b: { id: string }) =>
  a.id < b.id ? -1 : a.id > b.id ? 1 : 0;

function groupByService(containers: Iterable<CollectorContainer>) {
  const services = new Map<string, Service>();

  for (const container of containers) {
    const { compose } = container;
    const id = compose
      ? serviceId(compose.project, compose.service)
      : standaloneId(container.name);
    const service = services.get(id) ?? {
      id,
      label: compose ? compose.service : container.name,
      instances: [],
    };

    service.instances.push(container);
    services.set(id, service);
  }

  return services;
}

// The same port can be bound on 0.0.0.0 and on ::, which Docker reports twice.
function publishedPorts(instances: CollectorContainer[]) {
  const ports = new Map<string, PublishedPort>();

  for (const { ports: bindings } of instances) {
    for (const binding of bindings) {
      const key = `${binding.containerPort}/${binding.protocol}/${binding.hostPort}`;
      const loopbackOnly = isLoopback(binding.hostIp);
      const known = ports.get(key);

      if (known) {
        // Reachable as soon as one binding is.
        known.loopbackOnly = known.loopbackOnly && loopbackOnly;
      } else {
        ports.set(key, {
          containerPort: binding.containerPort,
          protocol: binding.protocol,
          hostPort: binding.hostPort,
          loopbackOnly,
        });
      }
    }
  }

  return [...ports.values()].sort(
    (a, b) =>
      a.hostPort - b.hostPort ||
      a.containerPort - b.containerPort ||
      a.protocol.localeCompare(b.protocol),
  );
}

// The whole graph, derived from the containers the collector reports. Only
// containers carry information: a network or a volume is a node when a
// container uses it, and an edge exists only when both of its ends do, so a
// snapshot never holds an orphan edge.
//
// Edges leave the one that depends and point to its dependency:
//   service -depends_on-> service   (Compose `depends_on`, same project)
//   service -connects_to-> network
//   service -mounts-> volume        (named volumes; bind mounts are redacted)
//   service -runs_on-> host
//   external -exposes-> service     (a port published beyond loopback)
export function buildGraph(
  environmentId: string,
  containers: Iterable<CollectorContainer>,
): Graph {
  const services = groupByService(containers);
  const nodes = new Map<string, GraphNode>();
  const edges = new Map<string, GraphEdge>();

  const addEdge = (
    kind: GraphEdge['kind'],
    source: string,
    target: string,
    metadata?: Record<string, unknown>,
  ) => {
    const id = edgeId(kind, source, target);

    edges.set(
      id,
      metadata
        ? { id, source, target, kind, metadata }
        : { id, source, target, kind },
    );
  };

  for (const service of services.values()) {
    // Best instance first: it gives the image shown for the service.
    const instances = [...service.instances].sort(
      (a, b) =>
        statusRank(containerStatus(a)) - statusRank(containerStatus(b)) ||
        a.name.localeCompare(b.name),
    );
    const [first] = instances;
    const ports = publishedPorts(instances);

    nodes.set(service.id, {
      id: service.id,
      kind: 'service',
      label: service.label,
      status: bestStatus(instances.map(containerStatus)),
      metadata: {
        image: first.image,
        ...(first.compose && {
          composeProject: first.compose.project,
          composeService: first.compose.service,
        }),
        ports,
      },
    });

    for (const instance of instances) {
      const { compose } = instance;

      if (compose) {
        for (const dependency of compose.dependsOn) {
          const target = serviceId(compose.project, dependency);

          if (target !== service.id && services.has(target)) {
            addEdge('depends_on', service.id, target);
          }
        }
      }

      for (const network of instance.networks) {
        const target = networkNodeId(network.name);

        nodes.set(target, {
          id: target,
          kind: 'network',
          label: network.name,
          status: 'healthy',
        });
        addEdge('connects_to', service.id, target);
      }
    }

    // A volume is mounted read-only only if every mount of it is.
    const volumes = new Map<string, boolean>();

    for (const { mounts } of instances) {
      for (const mount of mounts) {
        if (mount.type === 'volume') {
          volumes.set(
            mount.source,
            (volumes.get(mount.source) ?? true) && mount.readOnly,
          );
        }
      }
    }

    for (const [name, readOnly] of volumes) {
      const target = volumeNodeId(name);

      nodes.set(target, {
        id: target,
        kind: 'volume',
        label: name,
        status: 'healthy',
      });
      addEdge('mounts', service.id, target, { readOnly });
    }

    const exposed = ports.filter((port) => !port.loopbackOnly);

    if (exposed.length > 0) {
      nodes.set(EXTERNAL_NODE_ID, {
        id: EXTERNAL_NODE_ID,
        kind: 'external',
        label: 'Internet',
        // Nothing is known about the outside.
        status: 'unknown',
      });
      addEdge('exposes', EXTERNAL_NODE_ID, service.id, {
        ports: exposed.map(({ containerPort, protocol, hostPort }) => ({
          containerPort,
          protocol,
          hostPort,
        })),
      });
    }
  }

  if (services.size > 0) {
    const host = hostNodeId(environmentId);

    nodes.set(host, {
      id: host,
      kind: 'host',
      label: 'Docker host',
      status: 'healthy',
    });

    for (const service of services.values()) {
      addEdge('runs_on', service.id, host);
    }
  }

  return {
    nodes: [...nodes.values()].sort(byId),
    edges: [...edges.values()].sort(byId),
  };
}
