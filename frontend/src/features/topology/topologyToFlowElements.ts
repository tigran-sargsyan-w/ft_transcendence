import dagre from "@dagrejs/dagre";
import { MarkerType, type Edge, type Node } from "@xyflow/react";
import type { ServiceNodeData } from "./ServiceNode";
import type { TopologySnapshot } from "./types";

const NODE_WIDTH = 160;
const NODE_HEIGHT = 50;
const ISOLATED_GAP = 24;
const ISOLATED_COLUMNS = 4;
const ISOLATED_TOP_MARGIN = 60;

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
  const linked = new Set(
    topology.edges.flatMap((edge) => [edge.source, edge.target]),
  );
  const connected = topology.nodes.filter((node) => linked.has(node.id));
  const isolated = topology.nodes.filter((node) => !linked.has(node.id));

  const graph = layout({ ...topology, nodes: connected });
  const graphWidth = connected.length > 0 ? (graph.graph().width ?? 0) : 0;
  const isolatedTop =
    connected.length > 0
      ? (graph.graph().height ?? 0) + ISOLATED_TOP_MARGIN
      : 0;

  const columns = Math.min(isolated.length, ISOLATED_COLUMNS);
  const rowWidth = columns * NODE_WIDTH + (columns - 1) * ISOLATED_GAP;
  const isolatedLeft = Math.max(0, (graphWidth - rowWidth) / 2);

  const positionOf = (id: string) => {
    if (linked.has(id)) {
      const { x, y } = graph.node(id);
      return { x: x - NODE_WIDTH / 2, y: y - NODE_HEIGHT / 2 };
    }
    const index = isolated.findIndex((node) => node.id === id);
    return {
      x:
        isolatedLeft +
        (index % ISOLATED_COLUMNS) * (NODE_WIDTH + ISOLATED_GAP),
      y:
        isolatedTop +
        Math.floor(index / ISOLATED_COLUMNS) * (NODE_HEIGHT + ISOLATED_GAP),
    };
  };

  const nodes: Node<ServiceNodeData, "service">[] = topology.nodes.map(
    (node) => {
      return {
        id: node.id,
        type: "service",
        position: positionOf(node.id),
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
