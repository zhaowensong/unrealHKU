#!/usr/bin/env python3
"""Audit topology using trusted lanes plus complete manual connector chains.

This report is intentionally stricter than the live search planner: accepted
prefixes from failed attempts and generated proximity connectors never count.
Only a manual source with one full accepted segment chain is included, and the
report does not itself change runtime trust or publish a cache.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import certify_central_network_with_cesium as certifier
import recover_central_semantic_detours_with_cesium as recovery


WORK_PATH = SCRIPT_DIR / "Data/central_network_certification_working.json"
PROJECTED_PATH = SCRIPT_DIR / "Data/central_pedestrian_source_unreal.json"
CORRECTIONS_PATH = SCRIPT_DIR / "central_manual_corrections.json"
REPORT_PATH = SCRIPT_DIR.parents[2] / "Saved/Reports/central_completed_connectors_latest.json"


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def build_report() -> dict[str, Any]:
    projected = load_json(PROJECTED_PATH)
    corrections = load_json(CORRECTIONS_PATH)
    work = load_json(WORK_PATH)
    plan = certifier.build_source_plan(projected, corrections)
    plan["candidates"].extend(work.get("generated_connector_candidates", []))
    semantic = work.get("semantic_recovery_candidates", [])
    certifier._register_semantic_nodes(plan, semantic)
    plan["candidates"].extend(semantic)
    candidate_by_id = {
        candidate["candidate_id"]: candidate for candidate in plan["candidates"]
    }
    completed_source_ids = recovery.completed_manual_connector_source_ids(
        semantic, work["results"]
    )
    trusted_ids = certifier.trusted_runtime_result_ids(plan, work["results"])
    complete_manual_ids = {
        candidate_id
        for candidate_id, result in work["results"].items()
        if result.get("status") == "accepted"
        and candidate_id in candidate_by_id
        and candidate_by_id[candidate_id].get("topology_origin")
        == recovery.MANUAL_CONNECTOR_ORIGIN
        and candidate_by_id[candidate_id].get("semantic_source_candidate_id")
        in completed_source_ids
    }
    admitted_ids = set(trusted_ids) | complete_manual_ids
    components = certifier.accepted_components(
        plan,
        work["results"],
        allowed_origins=set(certifier.RUNTIME_ELIGIBLE_TOPOLOGY_ORIGINS),
        allowed_candidate_ids=admitted_ids,
    )
    metrics = []
    for component in components:
        coverage = certifier.certified_component_coverage(component)
        coverage["passes_3_4"] = bool(
            len(coverage["cell_ids"]) == certifier.CELL_COUNT
            and coverage["directional_lane_length_cm"]
            >= certifier.MINIMUM_TRUSTED_UNDIRECTED_LENGTH_CM * 2.0
            and coverage["street_block_count"]
            >= certifier.MINIMUM_CONNECTED_STREET_BLOCKS
            and coverage["junction_count"]
            >= certifier.MINIMUM_CERTIFIED_JUNCTIONS
        )
        coverage.pop("candidate_ids", None)
        metrics.append(coverage)
    metrics.sort(
        key=lambda item: (
            -len(item["cell_ids"]),
            -item["directional_lane_length_cm"],
            -item["street_block_count"],
            -item["junction_count"],
        )
    )
    return {
        "schema_version": 1,
        "read_only": True,
        "generated_connectors_included": False,
        "incomplete_manual_prefixes_included": False,
        "trusted_candidate_count": len(trusted_ids),
        "completed_manual_source_count": len(completed_source_ids),
        "completed_manual_source_ids": sorted(completed_source_ids),
        "completed_manual_candidate_count": len(complete_manual_ids),
        "admitted_component_count": len(components),
        "qualifying_component_count": sum(row["passes_3_4"] for row in metrics),
        "top_components": metrics[:12],
    }


def main() -> int:
    report = build_report()
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    compact = {
        key: report[key]
        for key in (
            "completed_manual_source_count",
            "completed_manual_candidate_count",
            "admitted_component_count",
            "qualifying_component_count",
        )
    }
    compact["largest_component"] = report["top_components"][0] if report["top_components"] else None
    compact["report"] = str(REPORT_PATH.resolve())
    print("CENTRAL_COMPLETED_CONNECTORS=" + json.dumps(compact, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
