#!/usr/bin/env python3
"""Verify the Ground-Only Central cache and its deterministic parent derivation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import central_certified_import_common as common
import derive_central_ground_only_network as ground_only


ROOT = Path(__file__).resolve().parent
DEFAULT_CACHE = ROOT / "Data" / "central_network_ground_only.json"
DEFAULT_PARENT = ROOT / "Data" / "central_network_certified.json"
DEFAULT_SOURCE = ROOT / "Data" / "central_pedestrian_source.json"
DEFAULT_POLICY = ROOT / "central_ground_only_policy.json"
DEFAULT_SCHEMA = ROOT / "central_network_certified.schema.json"


def validate_ground_only_document(
    document: dict[str, Any],
    parent: dict[str, Any],
    source: dict[str, Any],
    policy: dict[str, Any],
    parent_path: Path,
    policy_path: Path,
    schema: dict[str, Any],
) -> dict[str, Any]:
    metrics = common.validate_certified_document(document, schema)
    expected, audit = ground_only.derive(
        parent, source, policy, parent_path, policy_path
    )
    if common.sha256_json(document) != common.sha256_json(expected):
        raise common.CertifiedImportError(
            "Ground-Only cache is not the deterministic result of its recorded parent and policy"
        )
    filter_record = document.get("ground_only_filter")
    if not isinstance(filter_record, dict):
        raise common.CertifiedImportError("ground_only_filter is required")
    lanes = [lane for cell in document["cells"] for lane in cell["directed_lanes"]]
    if any(lane.get("ground_only_eligible") is not True for lane in lanes):
        raise common.CertifiedImportError("all imported lanes must explicitly be Ground-Only eligible")
    excluded_ids = {
        row["source_feature_id"] for row in filter_record["excluded_source_features"]
    }
    active_ids = {lane["source_feature_id"] for lane in lanes}
    if active_ids.intersection(excluded_ids):
        raise common.CertifiedImportError("excluded source feature remains in the runtime graph")
    if len(document["spawn_districts"]) != 6:
        raise common.CertifiedImportError("Ground-Only cache requires six spawn districts")
    metrics.update(
        {
            "ground_only_valid": True,
            "policy_id": filter_record["policy_id"],
            "policy_sha256": filter_record["policy_sha256"],
            "parent_file_sha256": filter_record["parent_file_sha256"],
            "parent_build_id": filter_record["parent_build_id"],
            "active_source_feature_count": len(active_ids),
            "excluded_source_feature_count": len(excluded_ids),
            "excluded_directional_lane_count": filter_record[
                "excluded_directional_lane_count"
            ],
            "directional_lane_length_cm": document["evidence"][
                "certified_directional_lane_length_cm"
            ],
            "zero_elevated_source_lanes": True,
            "derivation_audit": audit,
        }
    )
    return metrics


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--parent", type=Path, default=DEFAULT_PARENT)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    args = parser.parse_args()
    document = common.load_json_strict(args.cache)
    parent = common.load_json_strict(args.parent)
    source = common.load_json_strict(args.source)
    policy = common.load_json_strict(args.policy)
    schema = common.load_json_strict(args.schema)
    metrics = validate_ground_only_document(
        document, parent, source, policy, args.parent, args.policy, schema
    )
    report = {
        "status": "ok",
        "cache": str(args.cache),
        "network_id": document["network_id"],
        "build_id": document["build_id"],
        "policy_id": metrics["policy_id"],
        "policy_sha256": metrics["policy_sha256"],
        "parent_file_sha256": metrics["parent_file_sha256"],
        "lane_count": metrics["lane_count"],
        "component_count": metrics["component_count"],
        "spawn_district_count": metrics["spawn_district_count"],
        "target_population": metrics["target_population"],
        "active_source_feature_count": metrics["active_source_feature_count"],
        "excluded_source_feature_count": metrics["excluded_source_feature_count"],
        "excluded_directional_lane_count": metrics["excluded_directional_lane_count"],
        "directional_lane_length_cm": metrics["directional_lane_length_cm"],
        "zero_elevated_source_lanes": metrics["zero_elevated_source_lanes"],
        "canonical_file_sha256": common.file_sha256(args.cache),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
