import json
from pathlib import Path

from app.attack_paths import compute_attack_paths
from app.blast_radius import build_digraph
from app.schemas import GraphEdge, GraphNode, TopologySnapshot

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


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


def test_attack_paths_ignores_non_dependency_edges():
    topology = TopologySnapshot(
        schemaVersion=1,
        environmentId="env_test",
        capturedAt="2026-09-14T10:00:00Z",
        nodes=[
            GraphNode(id="svc_api", kind="service", label="api", status="healthy"),
            GraphNode(id="svc_db", kind="service", label="db", status="healthy"),
            GraphNode(
                id="svc_sidecar", kind="service", label="sidecar", status="healthy"
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
                id="edge_api_sidecar",
                source="svc_api",
                target="svc_sidecar",
                kind="connects_to",
            ),
        ],
    )
    result = compute_attack_paths(build_digraph(topology), ["svc_api"])
    path_node_ids = [tuple(path["nodeIds"]) for path in result["paths"]]
    assert path_node_ids == [("svc_api", "svc_db")]
    assert all(path["edgeIds"] == ["edge_api_db"] for path in result["paths"])


def _mixed_kind_topology():
    return TopologySnapshot(
        **json.loads((FIXTURES / "mixed_kind_topology.json").read_text())
    )


def test_attack_paths_from_internet_follow_exposes_then_depends_on():
    result = compute_attack_paths(
        build_digraph(_mixed_kind_topology()),
        ["ext:internet"],
    )
    path_node_ids = [tuple(path["nodeIds"]) for path in result["paths"]]
    assert ("ext:internet", "svc:demo:web") in path_node_ids
    assert ("ext:internet", "svc:demo:web", "svc:demo:api") in path_node_ids
    assert (
        "ext:internet",
        "svc:demo:web",
        "svc:demo:api",
        "svc:demo:db",
    ) in path_node_ids


def test_attack_paths_ignore_mounts_runs_on_and_connects_to():
    result = compute_attack_paths(
        build_digraph(_mixed_kind_topology()),
        ["svc:demo:web"],
    )
    path_node_ids = [tuple(path["nodeIds"]) for path in result["paths"]]
    assert path_node_ids == [
        ("svc:demo:web", "svc:demo:api"),
        ("svc:demo:web", "svc:demo:api", "svc:demo:db"),
    ]
    assert all(
        "host:env_local_compose" not in path["nodeIds"]
        and "net:demo_default" not in path["nodeIds"]
        and "vol:demo_db_data" not in path["nodeIds"]
        for path in result["paths"]
    )
