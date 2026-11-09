import json
from pathlib import Path

import pytest
from fastapi.responses import JSONResponse

from app.main import analyze
from app.schemas import AnalyzeOptions, AnalyzeRequest, TopologySnapshot

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
GOLDEN = FIXTURES / "golden"


def _load_json(path: Path):
    return json.loads(path.read_text())


def _without_time(response: dict) -> dict:
    payload = json.loads(json.dumps(response))
    payload["data"].pop("analyzedAt", None)
    return payload


def _analyze(topology: dict, analyses: list, seeds=None):
    options = (
        AnalyzeOptions(seedNodeIds=seeds) if seeds is not None else None
    )
    response = analyze(
        AnalyzeRequest(
            topology=TopologySnapshot(**topology),
            analyses=analyses,
            options=options,
        )
    )
    assert not isinstance(response, JSONResponse), getattr(
        response, "body", response
    )
    return response


@pytest.mark.parametrize(
    ("golden_name", "analyses", "seeds"),
    [
        ("sample_blast_radius.json", ["blast_radius"], ["svc_db"]),
        ("sample_attack_paths.json", ["attack_paths"], ["svc_api"]),
        ("sample_critical_nodes.json", ["critical_nodes"], None),
    ],
)
def test_analyze_matches_golden(golden_name, analyses, seeds):
    topology = _load_json(FIXTURES / "sample_topology.json")
    expected = _load_json(GOLDEN / golden_name)
    assert _without_time(_analyze(topology, analyses, seeds)) == _without_time(
        expected
    )


@pytest.mark.parametrize(
    "fixture_path",
    sorted(FIXTURES.glob("*_topology.json")),
    ids=lambda path: path.name,
)
def test_topology_fixtures_analyze_critical_nodes(fixture_path: Path):
    fixture = _load_json(fixture_path)
    response = _analyze(fixture, ["critical_nodes"])
    assert response["data"].get("topologyRevision") == fixture.get(
        "topologyRevision"
    )
    assert "criticalNodes" in response["data"]["results"]
