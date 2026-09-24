from typing import Dict, List

import networkx as nx


def compute_critical_nodes(graph: nx.DiGraph) -> Dict:
    """
    Rank nodes by how critical they are in the topology.

    v0 metric mix:
    - betweenness centrality (bridge / bottleneck role)
    - in-degree (how many others depend on this node for depends_on-style edges)
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

    betweenness = nx.betweenness_centrality(graph, normalized=True)
    max_in_degree = max((graph.in_degree(n) for n in node_ids), default=0)

    ranked: List[Dict] = []
    for node_id in node_ids:
        betweenness_score = float(betweenness.get(node_id, 0.0))
        in_degree = graph.in_degree(node_id)
        dependents_score = (
            float(in_degree) / float(max_in_degree) if max_in_degree > 0 else 0.0
        )
        score = round(0.6 * betweenness_score + 0.4 * dependents_score, 4)

        reasons: List[str] = []
        if betweenness_score >= 0.3:
            reasons.append("high_betweenness")
        if max_in_degree > 0 and in_degree >= max(1, int(0.5 * max_in_degree)):
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
