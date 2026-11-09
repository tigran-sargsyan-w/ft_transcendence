import type { GraphNode, NodeStatus } from './types'

// Worst first: the services that need attention come at the top.
export const STATUS_ORDER: NodeStatus[] = [
  'compromised',
  'down',
  'degraded',
  'unknown',
  'healthy',
]

const statusRank = (status: NodeStatus) => STATUS_ORDER.indexOf(status)

/** Service nodes only, worst status first, then by name. */
export function getServices(nodes: GraphNode[]): GraphNode[] {
  return nodes
    .filter((node) => node.kind === 'service')
    .sort(
      (a, b) =>
        statusRank(a.status) - statusRank(b.status) ||
        a.label.localeCompare(b.label),
    )
}

/** How many nodes have each status (every status present, 0 when none). */
export function countByStatus(nodes: GraphNode[]): Record<NodeStatus, number> {
  const counts = Object.fromEntries(
    STATUS_ORDER.map((status) => [status, 0]),
  ) as Record<NodeStatus, number>
  for (const node of nodes) counts[node.status]++
  return counts
}
