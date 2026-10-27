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


def test_critical_nodes_ignores_non_dependency_edges_for_dependents():
    topology = _topology(
        nodes=[
            GraphNode(id="svc_api", kind="service", label="api", status="healthy"),
            GraphNode(id="svc_db", kind="service", label="db", status="healthy"),
            GraphNode(
                id="svc_metrics", kind="service", label="metrics", status="healthy"
            ),
            GraphNode(id="svc_a", kind="service", label="a", status="healthy"),
            GraphNode(id="svc_b", kind="service", label="b", status="healthy"),
        ],
        edges=[
            GraphEdge(
                id="edge_api_db",
                source="svc_api",
                target="svc_db",
                kind="depends_on",
            ),
            GraphEdge(
                id="edge_a_metrics",
                source="svc_a",
                target="svc_metrics",
                kind="connects_to",
            ),
            GraphEdge(
                id="edge_b_metrics",
                source="svc_b",
                target="svc_metrics",
                kind="connects_to",
            ),
            GraphEdge(
                id="edge_api_metrics",
                source="svc_api",
                target="svc_metrics",
                kind="exposes",
            ),
        ],
    )
    result = compute_critical_nodes(build_digraph(topology))
    by_id = {item["nodeId"]: item for item in result["nodes"]}

    assert "many_dependents" in by_id["svc_db"]["reasons"]
    assert "many_dependents" not in by_id["svc_metrics"]["reasons"]


def test_dependency_chain_bridge_and_transitive_reasons():
    # web -> api -> db : api is the pivot; db has the largest blast impact.
    topology = _topology(
        nodes=[
            GraphNode(id="web", kind="service", label="web", status="healthy"),
            GraphNode(id="api", kind="service", label="api", status="healthy"),
            GraphNode(id="db", kind="service", label="db", status="healthy"),
        ],
        edges=[
            GraphEdge(
                id="edge_web_api",
                source="web",
                target="api",
                kind="depends_on",
            ),
            GraphEdge(
                id="edge_api_db",
                source="api",
                target="db",
                kind="depends_on",
            ),
        ],
    )
    by_id = {
        item["nodeId"]: item
        for item in compute_critical_nodes(build_digraph(topology))["nodes"]
    }

    assert "high_betweenness" in by_id["api"]["reasons"]
    assert "many_dependents" in by_id["db"]["reasons"]
    assert "exposed_to_internet" not in by_id["web"]["reasons"]


def test_mixed_kind_ranking_prefers_api_bridge_and_flags_exposure():
    # Nest-style mixed edges: betweenness uses linked depends_on nodes only (not
    # diluted by host/net/volume) - api ranks above db; web is internet-facing.
    topology = _topology(
        nodes=[
            GraphNode(id="web", kind="service", label="web", status="healthy"),
            GraphNode(id="api", kind="service", label="api", status="healthy"),
            GraphNode(id="db", kind="service", label="db", status="healthy"),
            GraphNode(id="net", kind="network", label="net", status="healthy"),
            GraphNode(id="vol", kind="volume", label="vol", status="healthy"),
            GraphNode(id="host", kind="host", label="host", status="healthy"),
            GraphNode(
                id="ext:internet",
                kind="external",
                label="Internet",
                status="unknown",
            ),
        ],
        edges=[
            GraphEdge(
                id="depends_on:web>api",
                source="web",
                target="api",
                kind="depends_on",
            ),
            GraphEdge(
                id="depends_on:api>db",
                source="api",
                target="db",
                kind="depends_on",
            ),
            GraphEdge(
                id="connects_to:web>net",
                source="web",
                target="net",
                kind="connects_to",
            ),
            GraphEdge(
                id="mounts:db>vol",
                source="db",
                target="vol",
                kind="mounts",
            ),
            GraphEdge(
                id="runs_on:web>host",
                source="web",
                target="host",
                kind="runs_on",
            ),
            GraphEdge(
                id="runs_on:api>host",
                source="api",
                target="host",
                kind="runs_on",
            ),
            GraphEdge(
                id="runs_on:db>host",
                source="db",
                target="host",
                kind="runs_on",
            ),
            GraphEdge(
                id="exposes:ext>web",
                source="ext:internet",
                target="web",
                kind="exposes",
            ),
        ],
    )
    result = compute_critical_nodes(build_digraph(topology))
    by_id = {item["nodeId"]: item for item in result["nodes"]}

    assert by_id["api"]["score"] > by_id["db"]["score"]
    assert by_id["api"]["score"] > by_id["web"]["score"]
    assert "high_betweenness" in by_id["api"]["reasons"]
    assert "many_dependents" in by_id["db"]["reasons"]
    assert "exposed_to_internet" in by_id["web"]["reasons"]
    assert "high_betweenness" not in by_id["web"]["reasons"]
    assert "many_dependents" not in by_id["host"]["reasons"]
    assert "many_dependents" not in by_id["net"]["reasons"]
