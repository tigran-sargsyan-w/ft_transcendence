import dagre from "@dagrejs/dagre";
import { MarkerType, type Edge, type Node } from "@xyflow/react";
import type { ServiceNodeData } from "./ServiceNode";
import type { TopologySnapshot } from "./types";

const NODE_WIDTH = 160;
const NODE_HEIGHT = 50;

function layout(topology: TopologySnapshot) {
  const graph = new dagre.graphlib.Graph();
  graph.setDefaultEdgeLabel(() => ({}));
  graph.setGraph({ rankdir: "TB", nodesep: 24, ranksep: 60 });

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

  const statusById = new Map(topology.nodes.map((n) => [n.id, n.status]));

  const edges: Edge[] = topology.edges.map((edge) => {
    const isDown = statusById.get(edge.target) === "down";
    const color = isDown ? "var(--health-down)" : "var(--muted-foreground)";
    return {
      id: edge.id,
      source: edge.source,
      target: edge.target,
      type: "smoothstep",
      animated: isDown,
      style: { stroke: color, strokeWidth: 1.5 },
      markerEnd: { type: MarkerType.ArrowClosed, color },
    };
  });

  return { nodes, edges };
}
