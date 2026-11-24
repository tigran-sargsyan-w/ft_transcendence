import dagre from "@dagrejs/dagre";
import { MarkerType, type Edge, type Node } from "@xyflow/react";
import type { ServiceNodeData } from "./ServiceNode";
import type { TopologySnapshot } from "./types";

// Approximate size of ServiceNode, used by dagre to space the nodes.
const NODE_WIDTH = 160;
const NODE_HEIGHT = 70;

// Places the nodes in levels from left to right, following the edges.
function layout(topology: TopologySnapshot) {
  const graph = new dagre.graphlib.Graph();
  graph.setDefaultEdgeLabel(() => ({}));
  graph.setGraph({ rankdir: "LR", nodesep: 30, ranksep: 80 });

  topology.nodes.forEach((node) =>
    graph.setNode(node.id, { width: NODE_WIDTH, height: NODE_HEIGHT }),
  );
  topology.edges.forEach((edge) => graph.setEdge(edge.source, edge.target));

  dagre.layout(graph);
  return graph;
}

// Map contract topology ids to React Flow elements (stable backend ids)
export function topologyToFlowElements(topology: TopologySnapshot): {
  nodes: Node<ServiceNodeData, "service">[];
  edges: Edge[];
} {
  const graph = layout(topology);

  const nodes: Node<ServiceNodeData, "service">[] = topology.nodes.map(
    (node) => {
      // dagre gives the center of the node, React Flow expects its top-left corner.
      const { x, y } = graph.node(node.id);
      return {
        id: node.id,
        type: "service",
        position: {
          x: x - NODE_WIDTH / 2,
          y: y - NODE_HEIGHT / 2,
        },
        data: {
          label: node.label,
          kind: node.kind,
          status: node.status,
        },
      };
    },
  );

  const edges: Edge[] = topology.edges.map((edge) => ({
    id: edge.id,
    source: edge.source,
    target: edge.target,
    type: "smoothstep",
    markerEnd: { type: MarkerType.ArrowClosed },
  }));

  return { nodes, edges };
}
