import type { EngineTopology, TopologySnapshot } from './graph.types.js';

// What the Graph Engine echoes back as `topologyRevision`, so Nest can tell a
// result computed on an older topology from one computed on the current one.
// The epoch comes first: a restart of Nest starts the revision over at 1, and
// a result of its previous life must not look current.
export const topologyRevisionOf = ({
  epoch,
  revision,
}: Pick<TopologySnapshot, 'epoch' | 'revision'>) => `${epoch}:${revision}`;

// The `topology` of a POST /analyze, field by field. The engine ignores the
// fields it does not know, `epoch` and `revision` among them, and says
// nothing; it would then echo no `topologyRevision`, and the stale check
// would be off without any error.
export function toEngineTopology(snapshot: TopologySnapshot): EngineTopology {
  return {
    schemaVersion: snapshot.schemaVersion,
    environmentId: snapshot.environmentId,
    capturedAt: snapshot.capturedAt,
    topologyRevision: topologyRevisionOf(snapshot),
    nodes: snapshot.nodes,
    edges: snapshot.edges,
  };
}
