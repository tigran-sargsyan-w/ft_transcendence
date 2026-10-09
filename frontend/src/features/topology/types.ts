export type NodeKind = 'service' | 'network' | 'volume' | 'host' | 'external'

export type NodeStatus =
  | 'healthy'
  | 'degraded'
  | 'down'
  | 'unknown'
  | 'compromised'

export type EdgeKind =
  | 'depends_on'
  | 'connects_to'
  | 'exposes'
  | 'runs_on'
  | 'mounts'

export type GraphNode = {
  id: string
  kind: NodeKind
  label: string
  status: NodeStatus
  metadata?: Record<string, unknown>
}

export type GraphEdge = {
  id: string
  source: string
  target: string
  kind: EdgeKind
  metadata?: Record<string, unknown>
}

export type TopologySnapshot = {
  schemaVersion: 1
  environmentId: string
  capturedAt: string
  topologyRevision?: string
  nodes: GraphNode[]
  edges: GraphEdge[]
}
