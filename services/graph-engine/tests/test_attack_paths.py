from app.attack_paths import compute_attack_paths
from app.blast_radius import build_digraph
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


def test_attack_paths_from_api_are_forward():
    result = compute_attack_paths(build_digraph(_sample_topology()), ["svc_api"])
    path_node_ids = [tuple(path["nodeIds"]) for path in result["paths"]]
    assert ("svc_api", "svc_db") in path_node_ids
    assert ("svc_api", "svc_cache") in path_node_ids
    assert all(path["nodeIds"][0] == "svc_api" for path in result["paths"])


def test_attack_paths_respects_max_paths():
    result = compute_attack_paths(
        build_digraph(_sample_topology()),
        ["svc_api"],
        max_paths=1,
    )
    assert len(result["paths"]) == 1
