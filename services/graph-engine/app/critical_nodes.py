from typing import Dict, List

import networkx as nx

from app.blast_radius import dependency_subgraph
from app.schemas import DEPENDENCY_EDGE_KINDS

# critical_nodes v1 scoring (security risk map)
WEIGHT_BETWEENNESS = 0.6
WEIGHT_DEPENDENTS = 0.4
THRESHOLD_HIGH_BETWEENNESS = 0.3
THRESHOLD_MANY_DEPENDENTS_RATIO = 0.5


def _dependency_in_degree(graph: nx.DiGraph, node_id: str) -> int:
    """Count predecessors linked by a dependency edge kind (e.g. depends_on)."""
    count = 0
    for predecessor in graph.predecessors(node_id):
        kind = graph.edges[predecessor, node_id].get("kind")
        if kind in DEPENDENCY_EDGE_KINDS:
            count += 1
    return count


def compute_critical_nodes(graph: nx.DiGraph) -> Dict:
    """
    Rank nodes by how critical they are in the topology.

    v1 metric mix (risk map):
    - betweenness on the depends_on subgraph only (bridge / pivot role)
    - dependency in-degree (how many others directly depend_on this node)
    """
    node_ids = list(graph.nodes())
    if not node_ids:
        return {"nodes": []}

    if len(node_ids) == 1:
        only_id = node_ids[0]
        return {
            "nodes": [
                {
                    "nodeId": only_id,
                    "score": 1.0,
                    "reasons": ["single_node"],
                }
            ]
        }

    dependency_graph = dependency_subgraph(graph)
    betweenness = nx.betweenness_centrality(dependency_graph, normalized=True)
    dependency_in_degrees = {
        node_id: _dependency_in_degree(graph, node_id) for node_id in node_ids
    }
    max_in_degree = max(dependency_in_degrees.values(), default=0)

    ranked: List[Dict] = []
    for node_id in node_ids:
        betweenness_score = float(betweenness.get(node_id, 0.0))
        in_degree = dependency_in_degrees[node_id]
        dependents_score = (
            float(in_degree) / float(max_in_degree) if max_in_degree > 0 else 0.0
        )
        score = round(
            WEIGHT_BETWEENNESS * betweenness_score
            + WEIGHT_DEPENDENTS * dependents_score,
            4,
        )

        reasons: List[str] = []
        if betweenness_score >= THRESHOLD_HIGH_BETWEENNESS:
            reasons.append("high_betweenness")
        if max_in_degree > 0 and in_degree >= max(
            1, int(THRESHOLD_MANY_DEPENDENTS_RATIO * max_in_degree)
        ):
            reasons.append("many_dependents")
        if not reasons and score > 0:
            reasons.append("structural_role")

        ranked.append(
            {
                "nodeId": node_id,
                "score": score,
                "reasons": reasons,
            }
        )

    ranked.sort(key=lambda item: (-item["score"], item["nodeId"]))
    return {"nodes": ranked}
