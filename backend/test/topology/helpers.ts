import type {
  Graph,
  TopologyOperation,
} from '../../src/modules/topology/graph.types.js';

const byId = (a: { id: string }, b: { id: string }) =>
  a.id < b.id ? -1 : a.id > b.id ? 1 : 0;

// What a client does with a delta. After every single operation, no edge may
// point to a node that is missing: that is what the operation order is for.
export function applyOperations(
  graph: Graph,
  operations: TopologyOperation[],
): Graph {
  const nodes = new Map(graph.nodes.map((node) => [node.id, node]));
  const edges = new Map(graph.edges.map((edge) => [edge.id, edge]));

  for (const operation of operations) {
    switch (operation.op) {
      case 'node.upsert':
        nodes.set(operation.node.id, operation.node);
        break;
      case 'node.remove':
        nodes.delete(operation.id);
        break;
      case 'edge.upsert':
        edges.set(operation.edge.id, operation.edge);
        break;
      case 'edge.remove':
        edges.delete(operation.id);
        break;
    }

    for (const edge of edges.values()) {
      if (!nodes.has(edge.source) || !nodes.has(edge.target)) {
        throw new Error(
          `edge ${edge.id} points to a missing node after ${operation.op}`,
        );
      }
    }
  }

  return {
    nodes: [...nodes.values()].sort(byId),
    edges: [...edges.values()].sort(byId),
  };
}
