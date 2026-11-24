import type {
  GraphNode,
  NodeStatus,
  TopologySnapshot,
  CriticalNodeRow,
} from "./types";
import { mockServiceDetails } from "./mockServiceDetails";

// Worst first: the services that need attention come at the top.
export const STATUS_ORDER: NodeStatus[] = [
  "compromised",
  "down",
  "degraded",
  "unknown",
  "healthy",
];

const statusRank = (status: NodeStatus) => STATUS_ORDER.indexOf(status);

/** Service nodes only, worst status first, then by name. */
export function getServices(nodes: GraphNode[]): GraphNode[] {
  return nodes
    .filter((node) => node.kind === "service")
    .sort(
      (a, b) =>
        statusRank(a.status) - statusRank(b.status) ||
        a.label.localeCompare(b.label),
    );
}

/** How many nodes have each status (every status present, 0 when none). */
export function countByStatus(nodes: GraphNode[]): Record<NodeStatus, number> {
  const counts = Object.fromEntries(
    STATUS_ORDER.map((status) => [status, 0]),
  ) as Record<NodeStatus, number>;
  for (const node of nodes) counts[node.status]++;
  return counts;
}

export function getHealthyServices(topology: TopologySnapshot): GraphNode[] {
  const healthyServices = topology.nodes.filter(
    (service) => service.status === "healthy",
  );

  return healthyServices;
}

export function getDownServices(topology: TopologySnapshot): GraphNode[] {
  const downServices = topology.nodes.filter(
    (service) => service.status === "down",
  );

  return downServices;
}

export function getImpactedServices(topology: TopologySnapshot): GraphNode[] {
  const downServicesId = topology.nodes
    .filter((service) => service.status === "down")
    .map((down) => down.id);

  const impactedServicesId = topology.edges
    .filter((edge) => downServicesId.includes(edge.target))
    .map((impacted) => impacted.source);

  const impactedServices = topology.nodes.filter((service) =>
    impactedServicesId.includes(service.id),
  );

  return impactedServices;
}

export function getCriticality(
  criticalNodes: CriticalNodeRow[],
  nodesId: string,
): number | undefined {
  const criticality = criticalNodes.find((n) => n.id == nodesId)?.score;
  return criticality;
}

export function getServiceDetails(node: GraphNode) {
  const details = mockServiceDetails[node.id];
  return {
    node,
    id: node.id,
    name: node.label,
    status: node.status,
    image: node.metadata?.image as string | undefined,
    role: details?.role,
    lastEvent: details?.lastEvent,
  };
}

export type DetailedService = ReturnType<typeof getServiceDetails>;
