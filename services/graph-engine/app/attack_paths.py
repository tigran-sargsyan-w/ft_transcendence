from typing import Dict, List, Optional

import networkx as nx


def compute_attack_paths(
    graph: nx.DiGraph,
    seed_node_ids: List[str],
    max_depth: Optional[int] = None,
    max_paths: Optional[int] = None,
) -> Dict:
    """
    From compromised seed node(s), list simple directed paths an attacker
    could follow along dependency edges (source -> target).

    Example: api --depends_on--> db means a path api -> db.
    """
    depth_limit = 5 if max_depth is None else max_depth
    path_limit = 20 if max_paths is None else max_paths
    paths: List[Dict] = []

    def dfs(current: str, node_path: List[str], edge_path: List[str]) -> None:
        if len(paths) >= path_limit:
            return

        if len(node_path) > 1:
            length = len(node_path) - 1
            paths.append(
                {
                    "nodeIds": list(node_path),
                    "edgeIds": list(edge_path),
                    "score": round(1.0 / (1 + length), 4),
                }
            )

        if (len(node_path) - 1) >= depth_limit:
            return

        for successor in graph.successors(current):
            if successor in node_path:
                continue
            edge_id = graph.edges[current, successor]["id"]
            node_path.append(successor)
            edge_path.append(edge_id)
            dfs(successor, node_path, edge_path)
            node_path.pop()
            edge_path.pop()
            if len(paths) >= path_limit:
                return

    for seed_id in seed_node_ids:
        if seed_id not in graph:
            continue
        dfs(seed_id, [seed_id], [])
        if len(paths) >= path_limit:
            break

    paths.sort(key=lambda path: (-path["score"], len(path["nodeIds"]), path["nodeIds"]))

    return {
        "seedNodeIds": list(seed_node_ids),
        "paths": paths,
    }
