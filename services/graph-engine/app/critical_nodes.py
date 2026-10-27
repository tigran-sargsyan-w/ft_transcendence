from typing import Dict, List, Set

import networkx as nx

from app.blast_radius import dependency_subgraph

# critical_nodes v1 scoring (security risk map)
WEIGHT_BETWEENNESS = 0.6
WEIGHT_DEPENDENTS = 0.4
THRESHOLD_HIGH_BETWEENNESS = 0.3
THRESHOLD_MANY_DEPENDENTS_RATIO = 0.5


def _transitive_dependent_counts(dependency_graph: nx.DiGraph) -> Dict[str, int]:
    """
    How many other nodes fail if this node fails (blast-radius size on
    reversed depends_on, excluding the node itself).
    """
    reversed_graph = dependency_graph.reverse(copy=False)
    return {
        node_id: len(nx.descendants(reversed_graph, node_id))
        for node_id in dependency_graph.nodes()
    }


def _exposed_node_ids(graph: nx.DiGraph) -> Set[str]:
    """Nodes targeted by an `exposes` edge (e.g. ext:internet -> service)."""
    exposed: Set[str] = set()
    for _source, target, data in graph.edges(data=True):
        if data.get("kind") == "exposes":
            exposed.add(target)
    return exposed


def compute_critical_nodes(graph: nx.DiGraph) -> Dict:
    """
    Rank nodes by how critical they are in the topology.

    v1 metric mix (risk map):
    - betweenness on nodes that participate in depends_on (degree > 0), so
      isolated host/network/volume nodes do not dilute normalization
    - transitive dependents on depends_on (impact if the node fails)
    - reason `exposed_to_internet` when an `exposes` edge points at the node
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
    linked = [node for node, degree in dependency_graph.degree() if degree > 0]
    betweenness = (
        nx.betweenness_centrality(dependency_graph.subgraph(linked), normalized=True)
        if linked
        else {}
    )
    dependent_counts = _transitive_dependent_counts(dependency_graph)
    max_dependents = max(dependent_counts.values(), default=0)
    exposed_ids = _exposed_node_ids(graph)

    ranked: List[Dict] = []
    for node_id in node_ids:
        betweenness_score = float(betweenness.get(node_id, 0.0))
        dependent_count = dependent_counts[node_id]
        dependents_score = (
            float(dependent_count) / float(max_dependents)
            if max_dependents > 0
            else 0.0
        )
        score = round(
            WEIGHT_BETWEENNESS * betweenness_score
            + WEIGHT_DEPENDENTS * dependents_score,
            4,
        )

        reasons: List[str] = []
        if betweenness_score >= THRESHOLD_HIGH_BETWEENNESS:
            reasons.append("high_betweenness")
        if max_dependents > 0 and dependent_count >= max(
            1, THRESHOLD_MANY_DEPENDENTS_RATIO * max_dependents
        ):
            reasons.append("many_dependents")
        if node_id in exposed_ids:
            reasons.append("exposed_to_internet")
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
