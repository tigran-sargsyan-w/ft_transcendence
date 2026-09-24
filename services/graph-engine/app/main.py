from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.attack_paths import compute_attack_paths
from app.blast_radius import build_digraph, compute_blast_radius
from app.schemas import (
    ALLOWED_ANALYSES,
    ALLOWED_EDGE_KINDS,
    ALLOWED_NODE_KINDS,
    ALLOWED_NODE_STATUSES,
    ANALYSES_REQUIRING_SEEDS,
    AnalyzeRequest,
)

app = FastAPI(title="Graph Engine", version="0.1.0")


def error_response(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message}},
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(
    _request: Request, exc: RequestValidationError
) -> JSONResponse:
    return error_response(
        400,
        "GRAPH_INVALID_TOPOLOGY",
        f"Invalid request body: {exc.errors()[0]['msg']}",
    )


@app.get("/api/v1/health")
def health() -> dict:
    return {"data": {"status": "ok"}}


@app.post("/api/v1/analyze", response_model=None)
def analyze(body: AnalyzeRequest) -> Any:
    topology = body.topology

    if topology.schemaVersion != 1:
        return error_response(
            400,
            "GRAPH_INVALID_TOPOLOGY",
            f"Unsupported schemaVersion: {topology.schemaVersion}",
        )

    for node in topology.nodes:
        if node.kind not in ALLOWED_NODE_KINDS:
            return error_response(
                400,
                "GRAPH_INVALID_TOPOLOGY",
                f"Unknown node kind: {node.kind}",
            )
        if node.status not in ALLOWED_NODE_STATUSES:
            return error_response(
                400,
                "GRAPH_INVALID_TOPOLOGY",
                f"Unknown node status: {node.status}",
            )

    node_ids = {node.id for node in topology.nodes}
    for edge in topology.edges:
        if edge.kind not in ALLOWED_EDGE_KINDS:
            return error_response(
                400,
                "GRAPH_INVALID_TOPOLOGY",
                f"Unknown edge kind: {edge.kind}",
            )
        if edge.source not in node_ids or edge.target not in node_ids:
            return error_response(
                400,
                "GRAPH_INVALID_TOPOLOGY",
                f"Edge {edge.id} references unknown node",
            )

    for analysis_id in body.analyses:
        if analysis_id not in ALLOWED_ANALYSES:
            return error_response(
                400,
                "GRAPH_UNKNOWN_ANALYSIS",
                f"Unknown analysis id: {analysis_id}",
            )

    seed_node_ids = (
        list(body.options.seedNodeIds)
        if body.options and body.options.seedNodeIds
        else []
    )
    max_depth: Optional[int] = body.options.maxDepth if body.options else None
    max_paths: Optional[int] = body.options.maxPaths if body.options else None

    for analysis_id in body.analyses:
        if analysis_id in ANALYSES_REQUIRING_SEEDS and not seed_node_ids:
            return error_response(
                400,
                "GRAPH_MISSING_SEEDS",
                f"Analysis {analysis_id} requires options.seedNodeIds",
            )

    for seed_id in seed_node_ids:
        if seed_id not in node_ids:
            return error_response(
                400,
                "GRAPH_INVALID_TOPOLOGY",
                f"Unknown seed node id: {seed_id}",
            )

    graph = build_digraph(topology)
    results: Dict[str, Any] = {}

    if "blast_radius" in body.analyses:
        results["blastRadius"] = compute_blast_radius(
            graph,
            seed_node_ids,
            max_depth=max_depth,
        )
    if "attack_paths" in body.analyses:
        results["attackPaths"] = compute_attack_paths(
            graph,
            seed_node_ids,
            max_depth=max_depth,
            max_paths=max_paths,
        )
    if "critical_nodes" in body.analyses:
        results["criticalNodes"] = {
            "nodes": [],
        }

    analyzed_at = (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )

    return {
        "data": {
            "schemaVersion": 1,
            "environmentId": topology.environmentId,
            "analyzedAt": analyzed_at,
            "results": results,
        }
    }
