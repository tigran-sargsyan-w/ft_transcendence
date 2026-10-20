import json
from pathlib import Path

from app.attack_paths import compute_attack_paths
from app.blast_radius import build_digraph, compute_blast_radius
from app.critical_nodes import compute_critical_nodes
from app.schemas import TopologySnapshot

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
GOLDEN = FIXTURES / "golden"


def _load_json(path: Path):
    return json.loads(path.read_text())


def _sample_topology() -> TopologySnapshot:
    return TopologySnapshot(**_load_json(FIXTURES / "sample_topology.json"))


def _analyze_envelope(topology: TopologySnapshot, results: dict) -> dict:
    data = {
        "schemaVersion": 1,
        "environmentId": topology.environmentId,
        "analyzedAt": "2026-09-14T10:00:01Z",
        "results": results,
    }
    if topology.topologyRevision is not None:
        data["topologyRevision"] = topology.topologyRevision
    return {"data": data}


def test_sample_blast_radius_matches_golden():
    topology = _sample_topology()
    results = {
        "blastRadius": compute_blast_radius(
            build_digraph(topology),
            ["svc_db"],
        )
    }
    assert _analyze_envelope(topology, results) == _load_json(
        GOLDEN / "sample_blast_radius.json"
    )


def test_sample_attack_paths_matches_golden():
    topology = _sample_topology()
    results = {
        "attackPaths": compute_attack_paths(
            build_digraph(topology),
            ["svc_api"],
        )
    }
    assert _analyze_envelope(topology, results) == _load_json(
        GOLDEN / "sample_attack_paths.json"
    )


def test_sample_critical_nodes_matches_golden():
    topology = _sample_topology()
    results = {"criticalNodes": compute_critical_nodes(build_digraph(topology))}
    assert _analyze_envelope(topology, results) == _load_json(
        GOLDEN / "sample_critical_nodes.json"
    )
