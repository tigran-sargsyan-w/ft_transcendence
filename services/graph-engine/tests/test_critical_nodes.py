from app.blast_radius import build_digraph
from app.critical_nodes import compute_critical_nodes
from app.schemas import GraphEdge, GraphNode, TopologySnapshot


def _topology(nodes, edges):
    return TopologySnapshot(
        schemaVersion=1,
        environmentId="env_test",
        capturedAt="2026-09-14T10:00:00Z",
        nodes=nodes,
        edges=edges,
    )


def test_critical_nodes_empty_graph():
    topology = _topology(nodes=[], edges=[])
    result = compute_critical_nodes(build_digraph(topology))
    assert result == {"nodes": []}


def test_critical_nodes_single_node():
    topology = _topology(
        nodes=[
            GraphNode(
                id="svc_only",
                kind="service",
                label="only",
                status="healthy",
            )
        ],
        edges=[],
    )
    result = compute_critical_nodes(build_digraph(topology))
    assert result["nodes"] == [
        {
            "nodeId": "svc_only",
            "score": 1.0,
            "reasons": ["single_node"],
        }
    ]


def test_critical_nodes_sample_topology_ranks_api_high():
    topology = _topology(
        nodes=[
            GraphNode(id="svc_api", kind="service", label="api", status="down"),
            GraphNode(
                id="svc_worker", kind="service", label="worker", status="healthy"
            ),
            GraphNode(id="svc_db", kind="service", label="db", status="healthy"),
            GraphNode(
                id="svc_cache", kind="service", label="cache", status="healthy"
            ),
        ],
        edges=[
            GraphEdge(
                id="edge_api_db",
                source="svc_api",
                target="svc_db",
                kind="depends_on",
            ),
            GraphEdge(
                id="edge_api_cache",
                source="svc_api",
                target="svc_cache",
                kind="depends_on",
            ),
            GraphEdge(
                id="edge_worker_api",
                source="svc_worker",
                target="svc_api",
                kind="depends_on",
            ),
        ],
    )
    result = compute_critical_nodes(build_digraph(topology))
    node_ids = [item["nodeId"] for item in result["nodes"]]
    assert node_ids[0] == "svc_api"
    assert set(node_ids) == {"svc_api", "svc_worker", "svc_db", "svc_cache"}
