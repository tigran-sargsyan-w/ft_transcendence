import type { Edge, Node } from '@xyflow/react'
import type { TopologySnapshot } from './types'

/** Map contract topology ids to React Flow elements (stable backend ids). */
export function topologyToFlowElements(topology: TopologySnapshot): {
  nodes: Node[]
  edges: Edge[]
} {
  const nodes: Node[] = topology.nodes.map((node, index) => ({
    id: node.id,
    position: {
      x: (index % 3) * 220,
      y: Math.floor(index / 3) * 120,
    },
    data: {
      label: `${node.label} · ${node.status}`,
      kind: node.kind,
      status: node.status,
    },
  }))

  const edges: Edge[] = topology.edges.map((edge) => ({
    id: edge.id,
    source: edge.source,
    target: edge.target,
    label: edge.kind,
  }))

  return { nodes, edges }
}
