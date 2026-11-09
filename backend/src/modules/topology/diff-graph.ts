import type { Graph, TopologyOperation } from './graph.types.js';

// Graphs are built by one function with a fixed key order, so comparing
// their JSON is enough to tell whether a node or an edge changed.
const same = (a: unknown, b: unknown) =>
  JSON.stringify(a) === JSON.stringify(b);

function changes<T extends { id: string }>(before: T[], after: T[]) {
  const previous = new Map(before.map((item) => [item.id, item]));
  const current = new Set(after.map((item) => item.id));

  return {
    removed: before
      .filter((item) => !current.has(item.id))
      .map((item) => item.id),
    upserted: after.filter((item) => !same(previous.get(item.id), item)),
  };
}

// What turns `before` into `after`. Edges leave first and arrive last, so
// applied in order, an edge never points to a node that is not there.
export function diffGraph(before: Graph, after: Graph): TopologyOperation[] {
  const nodes = changes(before.nodes, after.nodes);
  const edges = changes(before.edges, after.edges);

  return [
    ...edges.removed.map((id) => ({ op: 'edge.remove' as const, id })),
    ...nodes.removed.map((id) => ({ op: 'node.remove' as const, id })),
    ...nodes.upserted.map((node) => ({ op: 'node.upsert' as const, node })),
    ...edges.upserted.map((edge) => ({ op: 'edge.upsert' as const, edge })),
  ];
}
