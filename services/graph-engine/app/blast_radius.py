from typing import Dict, List, Optional

import networkx as nx

from app.schemas import TopologySnapshot


def build_digraph(topology: TopologySnapshot) -> nx.DiGraph:
    graph = nx.DiGraph()
    for node in topology.nodes:
        graph.add_node(node.id)
    for edge in topology.edges:
        graph.add_edge(
            edge.source,
            edge.target,
            id=edge.id,
            kind=edge.kind,
        )
    return graph


def compute_blast_radius(
    graph: nx.DiGraph,
    seed_node_ids: List[str],
    max_depth: Optional[int] = None,
) -> Dict:
    """
    If a seed fails, find nodes that depend on it (directly or transitively).

    Contract edges are source -> target (e.g. api --depends_on--> db).
    Impact spreads to dependents, so we BFS on the reversed graph.
    """
    reversed_graph = graph.reverse(copy=False)
    depth_by_node_id: Dict[str, int] = {}
    queue: List[str] = []

    for seed_id in seed_node_ids:
        depth_by_node_id[seed_id] = 0
        queue.append(seed_id)

    index = 0
    while index < len(queue):
        current = queue[index]
        index += 1
        current_depth = depth_by_node_id[current]
        if max_depth is not None and current_depth >= max_depth:
            continue
        for neighbor in reversed_graph.successors(current):
            if neighbor in depth_by_node_id:
                continue
            depth_by_node_id[neighbor] = current_depth + 1
            queue.append(neighbor)

    affected_node_ids = sorted(
        depth_by_node_id.keys(),
        key=lambda node_id: (depth_by_node_id[node_id], node_id),
    )
    affected_set = set(affected_node_ids)

    affected_edge_ids: List[str] = []
    for source, target, data in graph.edges(data=True):
        if source in affected_set and target in affected_set:
            affected_edge_ids.append(data["id"])

    return {
        "seedNodeIds": list(seed_node_ids),
        "affectedNodeIds": affected_node_ids,
        "affectedEdgeIds": affected_edge_ids,
        "depthByNodeId": depth_by_node_id,
    }
