// Topology shapes of docs/architecture/graph-contract.md.

export type NodeKind = 'service' | 'network' | 'volume' | 'host' | 'external';

export type NodeStatus =
  'healthy' | 'degraded' | 'down' | 'unknown' | 'compromised';

export type EdgeKind =
  'depends_on' | 'connects_to' | 'exposes' | 'runs_on' | 'mounts';

export type GraphNode = {
  id: string;
  kind: NodeKind;
  label: string;
  status: NodeStatus;
  metadata?: Record<string, unknown>;
};

export type GraphEdge = {
  id: string;
  source: string;
  target: string;
  kind: EdgeKind;
  metadata?: Record<string, unknown>;
};

// Nodes and edges are sorted by id, so two equal graphs compare equal.
export type Graph = {
  nodes: GraphNode[];
  edges: GraphEdge[];
};

// The contract snapshot plus its position in the live stream: `epoch` names
// the in-memory state, `revision` counts the batches it applied.
export type TopologySnapshot = Graph & {
  schemaVersion: 1;
  environmentId: string;
  capturedAt: string;
  epoch: string;
  revision: number;
};

// A topology as the Graph Engine's analyze endpoint takes it: its revision is
// one string, and `epoch` and `revision` are not fields of it.
export type EngineTopology = Graph & {
  schemaVersion: 1;
  environmentId: string;
  capturedAt: string;
  topologyRevision: string;
};

export type TopologyOperation =
  | { op: 'node.upsert'; node: GraphNode }
  | { op: 'node.remove'; id: string }
  | { op: 'edge.upsert'; edge: GraphEdge }
  | { op: 'edge.remove'; id: string };

export type TopologyDelta = {
  epoch: string;
  baseRevision: number;
  revision: number;
  operations: TopologyOperation[];
};
