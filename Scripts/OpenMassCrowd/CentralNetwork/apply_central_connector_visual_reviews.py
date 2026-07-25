#!/usr/bin/env python3
"""Apply explicit Unreal visual-review decisions to certified connectors."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parents[2]
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import certify_central_network_with_cesium as certifier


REVIEW_PATH = SCRIPT_DIR / "central_connector_visual_reviews.json"
REPORT_PATH = PROJECT_ROOT / "Saved/Reports/central_connector_visual_reviews_latest.json"


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def apply_reviews() -> dict[str, Any]:
    review = read_json(REVIEW_PATH)
    work = read_json(certifier.WORK_PATH)
    generated = work.get("generated_connector_candidates", [])
    semantic = work.get("semantic_recovery_candidates", [])
    unresolved_semantic_ids = {
        candidate["candidate_id"]
        for candidate in semantic
        if candidate["candidate_id"] not in work.get("results", {})
    }
    if unresolved_semantic_ids:
        # Live recovery records a result immediately after appending a probed
        # segment.  A candidate without any result is therefore an unexecuted
        # planner/test artifact, not certified evidence.
        semantic[:] = [
            candidate
            for candidate in semantic
            if candidate["candidate_id"] not in unresolved_semantic_ids
        ]
    generated_by_id = {item["candidate_id"]: item for item in generated}
    semantic_by_source: dict[str, list[dict[str, Any]]] = {}
    for candidate in semantic:
        source_id = candidate.get("semantic_source_candidate_id")
        if isinstance(source_id, str):
            semantic_by_source.setdefault(source_id, []).append(candidate)

    applied_generated = []
    applied_manual_sources = []
    for decision in review.get("generated_connector_reviews", []):
        candidate_id = decision["candidate_id"]
        candidate = generated_by_id.get(candidate_id)
        if candidate is None:
            raise RuntimeError("reviewed generated connector is missing: " + candidate_id)
        status = decision["status"]
        if status not in {"accepted", "rejected"}:
            raise RuntimeError("invalid generated review status: " + status)
        evidence = PROJECT_ROOT / decision["evidence"]
        if not evidence.is_file():
            raise RuntimeError("review evidence is missing: " + str(evidence))
        if (
            status == "accepted"
            and work["results"].get(candidate_id, {}).get("status") != "accepted"
        ):
            raise RuntimeError("visual review cannot override failed Cesium certification")
        candidate["requires_manual_review"] = True
        candidate["manual_review_status"] = status
        candidate.setdefault("tags", {}).update(
            {
                "visual_review_evidence": decision["evidence"],
                "visual_review_reason": decision["reason"],
                "visual_reviewed_at_utc": review["reviewed_at_utc"],
                "visual_reviewer": review["reviewer"],
            }
        )
        applied_generated.append(candidate_id)

    for decision in review.get("manual_connector_source_reviews", []):
        source_id = decision["source_id"]
        candidates = semantic_by_source.get(source_id, [])
        if not candidates:
            raise RuntimeError("reviewed manual connector source is missing: " + source_id)
        status = decision["status"]
        if status not in {"accepted", "rejected"}:
            raise RuntimeError("invalid manual review status: " + status)
        evidence = PROJECT_ROOT / decision["evidence"]
        if not evidence.is_file():
            raise RuntimeError("review evidence is missing: " + str(evidence))
        for candidate in candidates:
            candidate["requires_manual_review"] = True
            candidate["manual_review_status"] = (
                "accepted"
                if status == "accepted"
                and work["results"].get(candidate["candidate_id"], {}).get("status")
                == "accepted"
                else "rejected"
            )
            candidate.setdefault("tags", {}).update(
                {
                    "visual_review_evidence": decision["evidence"],
                    "visual_review_reason": decision["reason"],
                    "visual_reviewed_at_utc": review["reviewed_at_utc"],
                    "visual_reviewer": review["reviewer"],
                }
            )
        applied_manual_sources.append(source_id)

    work["connector_visual_review"] = {
        "review_file": str(REVIEW_PATH.relative_to(PROJECT_ROOT)).replace("\\", "/"),
        "reviewed_at_utc": review["reviewed_at_utc"],
        "accepted_generated_connector_ids": sorted(
            decision["candidate_id"]
            for decision in review.get("generated_connector_reviews", [])
            if decision["status"] == "accepted"
        ),
        "rejected_generated_connector_ids": sorted(
            decision["candidate_id"]
            for decision in review.get("generated_connector_reviews", [])
            if decision["status"] == "rejected"
        ),
        "accepted_manual_connector_source_ids": sorted(
            decision["source_id"]
            for decision in review.get("manual_connector_source_reviews", [])
            if decision["status"] == "accepted"
        ),
        "rejected_manual_connector_source_ids": sorted(
            decision["source_id"]
            for decision in review.get("manual_connector_source_reviews", [])
            if decision["status"] == "rejected"
        ),
    }
    work["updated_at_utc"] = certifier.utc_timestamp()
    certifier.write_json_atomic(certifier.WORK_PATH, work)
    report = {
        "schema_version": 1,
        "status": "applied",
        "generated_decision_count": len(applied_generated),
        "generated_accepted_count": sum(
            decision["status"] == "accepted"
            for decision in review.get("generated_connector_reviews", [])
        ),
        "manual_source_decision_count": len(applied_manual_sources),
        "removed_unresolved_semantic_candidate_count": len(
            unresolved_semantic_ids
        ),
        "work_path": str(certifier.WORK_PATH),
        "review_path": str(REVIEW_PATH),
        "updated_at_utc": certifier.utc_timestamp(),
    }
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    certifier.write_json_atomic(REPORT_PATH, report)
    return report


def main() -> int:
    print(
        "CENTRAL_CONNECTOR_VISUAL_REVIEWS="
        + json.dumps(apply_reviews(), ensure_ascii=False, sort_keys=True)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
