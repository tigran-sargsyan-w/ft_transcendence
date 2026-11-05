from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


ALLOWED_NODE_KINDS = frozenset(
    {"service", "network", "volume", "host", "external"}
)
ALLOWED_NODE_STATUSES = frozenset(
    {"healthy", "degraded", "down", "unknown", "compromised"}
)
ALLOWED_EDGE_KINDS = frozenset(
    {"depends_on", "connects_to", "exposes", "runs_on", "mounts"}
)
# critical_nodes: depends_on only (betweenness + transitive dependents)
DEPENDENCY_EDGE_KINDS = frozenset({"depends_on"})
# blast_radius: impact via dependency / mount / host placement
BLAST_RADIUS_EDGE_KINDS = frozenset({"depends_on", "mounts", "runs_on"})
# attack_paths: reach via dependency or internet exposure entry
ATTACK_PATHS_EDGE_KINDS = frozenset({"depends_on", "exposes"})
ALLOWED_ANALYSES = frozenset(
    {"blast_radius", "attack_paths", "critical_nodes"}
)
ANALYSES_REQUIRING_SEEDS = frozenset({"blast_radius", "attack_paths"})


class GraphNode(BaseModel):
    id: str
    kind: str
    label: str
    status: str
    metadata: Optional[Dict[str, Any]] = None


class GraphEdge(BaseModel):
    id: str
    source: str
    target: str
    kind: str
    metadata: Optional[Dict[str, Any]] = None


class TopologySnapshot(BaseModel):
    schemaVersion: int
    environmentId: str
    capturedAt: str
    topologyRevision: Optional[str] = None
    nodes: List[GraphNode]
    edges: List[GraphEdge]


class AnalyzeOptions(BaseModel):
    seedNodeIds: Optional[List[str]] = None
    maxDepth: Optional[int] = None
    maxPaths: Optional[int] = None


class AnalyzeRequest(BaseModel):
    topology: TopologySnapshot
    analyses: List[str] = Field(min_length=1)
    options: Optional[AnalyzeOptions] = None
