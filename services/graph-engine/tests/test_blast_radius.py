from app.blast_radius import build_digraph, compute_blast_radius
from app.schemas import GraphEdge, GraphNode, TopologySnapshot


def _sample_topology():
    return TopologySnapshot(
        schemaVersion=1,
        environmentId="env_test",
        capturedAt="2026-09-14T10:00:00Z",
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


def test_blast_radius_from_db_includes_dependents():
    result = compute_blast_radius(build_digraph(_sample_topology()), ["svc_db"])
    assert result["affectedNodeIds"] == ["svc_db", "svc_api", "svc_worker"]
    assert result["depthByNodeId"]["svc_db"] == 0
    assert result["depthByNodeId"]["svc_api"] == 1
    assert result["depthByNodeId"]["svc_worker"] == 2
    assert set(result["affectedEdgeIds"]) == {"edge_api_db", "edge_worker_api"}
    assert "edge_api_cache" not in result["affectedEdgeIds"]


def test_blast_radius_respects_max_depth():
    result = compute_blast_radius(
        build_digraph(_sample_topology()),
        ["svc_db"],
        max_depth=1,
    )
    assert result["affectedNodeIds"] == ["svc_db", "svc_api"]
    assert "svc_worker" not in result["affectedNodeIds"]
