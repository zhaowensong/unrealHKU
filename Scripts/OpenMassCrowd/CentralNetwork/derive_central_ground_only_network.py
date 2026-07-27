#!/usr/bin/env python3
"""Derive an auditable Ground-Only view from the retained certified cache."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import uuid
from collections import Counter, defaultdict, deque
from pathlib import Path
from typing import Any

import central_certified_import_common as common


SCRIPT_VERSION = "1.0.0"
DATA_DIR = Path(__file__).resolve().parent / "Data"
DEFAULT_PARENT = DATA_DIR / "central_network_certified.json"
DEFAULT_SOURCE = DATA_DIR / "central_pedestrian_source.json"
DEFAULT_POLICY = Path(__file__).resolve().parent / "central_ground_only_policy.json"
DEFAULT_OUTPUT = DATA_DIR / "central_network_ground_only.json"
DEFAULT_AUDIT = DATA_DIR / "central_network_ground_only.audit.json"


def normalized(value: Any) -> str:
    return str(value).strip().lower()


def is_declared(value: Any) -> bool:
    return normalized(value) not in {"", "0", "false", "no", "none"}


def feature_exclusion_reasons(feature: dict[str, Any], policy: dict[str, Any]) -> list[str]:
    tags = feature.get("tags", {})
    reasons: list[str] = []
    feature_id = feature["feature_id"]
    parent_id = feature.get("parent_feature_id", feature_id)
    deny_ids = set(policy.get("manual_deny_source_ids", []))
    if feature_id in deny_ids or parent_id in deny_ids:
        reasons.append("manual-deny")
    if "bridge" in tags and is_declared(tags["bridge"]):
        reasons.append("bridge:" + normalized(tags["bridge"]))
    if policy.get("exclude_nonzero_layer", True) and "layer" in tags:
        try:
            nonzero_layer = abs(float(str(tags["layer"]).strip())) > 1.0e-9
        except ValueError:
            nonzero_layer = True
        if nonzero_layer:
            reasons.append("layer:" + normalized(tags["layer"]))
    if normalized(tags.get("highway", "")) in set(policy["excluded_highway_values"]):
        reasons.append("highway:" + normalized(tags["highway"]))
    if "tunnel" in tags and is_declared(tags["tunnel"]):
        reasons.append("tunnel:" + normalized(tags["tunnel"]))
    return sorted(set(reasons))


def component_id(lane_ids: list[str]) -> str:
    digest = hashlib.sha256("\n".join(sorted(lane_ids)).encode("utf-8")).hexdigest()
    return "ground-component-" + digest[:16]


def evidence(parent_count: int, lanes: list[dict[str, Any]], portals: list[dict[str, Any]],
             component_count: int, junction_count: int, street_block_count: int,
             district_count: int, geographic_blocks: int) -> dict[str, Any]:
    samples = sum(len(lane["ground_samples"]) for lane in lanes)
    lane_length = round(sum(float(lane["length_cm"]) for lane in lanes), 4)
    return {
        "candidate_lane_count": parent_count,
        "certified_lane_count": len(lanes),
        "rejected_lane_count": parent_count - len(lanes),
        "coarse_support_check_count": samples,
        "strict_ground_sample_count": samples,
        "exact_xy_support_pass_count": samples,
        "first_blocker_pass_count": samples,
        "height_continuity_pass_count": samples,
        "slope_pass_count": samples,
        "multi_track_support_pass_count": samples,
        "capsule_clearance_pass_count": samples,
        "missing_support_rejection_count": 0,
        "first_blocker_rejection_count": 0,
        "height_continuity_rejection_count": 0,
        "slope_rejection_count": 0,
        "multi_track_rejection_count": 0,
        "capsule_clearance_rejection_count": 0,
        "connected_component_count": component_count,
        "street_block_count": street_block_count,
        "certified_geographic_block_count": geographic_blocks,
        "junction_count": junction_count,
        "portal_count": len(portals),
        "spawn_district_count": district_count,
        "certified_directional_lane_length_cm": lane_length,
        "whole_area_recertification_count": 0,
    }


def derive(parent: dict[str, Any], source: dict[str, Any], policy: dict[str, Any],
           parent_path: Path, policy_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    features = {feature["feature_id"]: feature for feature in source["features"]}
    decisions = {
        feature_id: feature_exclusion_reasons(feature, policy)
        for feature_id, feature in features.items()
    }
    parent_cells = {cell["cell_id"]: cell for cell in parent["cells"]}
    parent_nodes = {
        node["node_id"]: node
        for cell in parent["cells"]
        for node in cell["nodes"]
    }
    parent_lanes = [lane for cell in parent["cells"] for lane in cell["directed_lanes"]]
    missing_sources = sorted({lane["source_feature_id"] for lane in parent_lanes} - features.keys())
    if missing_sources:
        raise RuntimeError(f"certified lanes have {len(missing_sources)} missing source features")

    kept_lanes = [copy.deepcopy(lane) for lane in parent_lanes if not decisions[lane["source_feature_id"]]]
    kept_lane_ids = {lane["lane_id"] for lane in kept_lanes}
    for lane in kept_lanes:
        if lane["reverse_lane_id"] not in kept_lane_ids:
            raise RuntimeError(f"Ground-Only filtering split a reverse pair: {lane['lane_id']}")
        lane["ground_only_eligible"] = True
    kept_node_ids = {
        node_id
        for lane in kept_lanes
        for node_id in (lane["from_node_id"], lane["to_node_id"])
    }

    undirected: dict[str, set[str]] = {node_id: set() for node_id in kept_node_ids}
    lanes_by_node_pair: dict[frozenset[str], list[str]] = defaultdict(list)
    for lane in kept_lanes:
        start, end = lane["from_node_id"], lane["to_node_id"]
        undirected[start].add(end)
        undirected[end].add(start)
        lanes_by_node_pair[frozenset((start, end))].append(lane["lane_id"])

    component_node_sets: list[set[str]] = []
    unseen = set(kept_node_ids)
    while unseen:
        root = min(unseen)
        reached = {root}
        pending = deque([root])
        unseen.remove(root)
        while pending:
            current = pending.popleft()
            for neighbor in sorted(undirected[current]):
                if neighbor in unseen:
                    unseen.remove(neighbor)
                    reached.add(neighbor)
                    pending.append(neighbor)
        component_node_sets.append(reached)

    lane_by_id = {lane["lane_id"]: lane for lane in kept_lanes}
    node_component: dict[str, str] = {}
    components: list[dict[str, Any]] = []
    for node_ids in component_node_sets:
        lane_ids = sorted(
            lane["lane_id"]
            for lane in kept_lanes
            if lane["from_node_id"] in node_ids
        )
        identifier = component_id(lane_ids)
        for node_id in node_ids:
            node_component[node_id] = identifier
        for lane_id in lane_ids:
            lane_by_id[lane_id]["component_id"] = identifier
        junctions = sum(1 for node_id in node_ids if parent_nodes[node_id]["kind"] == "junction")
        street_blocks = max(0, len(lane_ids) // 2 - len(node_ids) + 1)
        components.append({
            "component_id": identifier,
            "cell_ids": sorted({parent_nodes[node_id]["cell_id"] for node_id in node_ids}),
            "node_ids": sorted(node_ids),
            "directed_lane_ids": lane_ids,
            "topology_origins": sorted({lane_by_id[lane_id]["topology_origin"] for lane_id in lane_ids}),
            "directional_lane_length_cm": round(sum(float(lane_by_id[lane_id]["length_cm"]) for lane_id in lane_ids), 4),
            "junction_count": junctions,
            "street_block_count": street_blocks,
            "certified": True,
        })
    components.sort(key=lambda item: item["component_id"])

    incoming: dict[str, list[str]] = defaultdict(list)
    outgoing: dict[str, list[str]] = defaultdict(list)
    for lane in kept_lanes:
        outgoing[lane["from_node_id"]].append(lane["lane_id"])
        incoming[lane["to_node_id"]].append(lane["lane_id"])
    nodes: dict[str, dict[str, Any]] = {}
    for node_id in kept_node_ids:
        node = copy.deepcopy(parent_nodes[node_id])
        node["component_id"] = node_component[node_id]
        node["incoming_lane_ids"] = sorted(incoming[node_id])
        node["outgoing_lane_ids"] = sorted(outgoing[node_id])
        nodes[node_id] = node

    kept_portals = [
        copy.deepcopy(portal)
        for cell in parent["cells"]
        for portal in cell["portals"]
        if portal["directed_lane_id"] in kept_lane_ids
    ]
    kept_portal_ids = {portal["portal_id"] for portal in kept_portals}
    for portal in kept_portals:
        if portal["reverse_portal_id"] not in kept_portal_ids:
            raise RuntimeError(f"Ground-Only filtering split a portal pair: {portal['portal_id']}")

    cells: list[dict[str, Any]] = []
    for cell_id in sorted(parent_cells):
        parent_cell = parent_cells[cell_id]
        cell_lanes = sorted(
            (lane for lane in kept_lanes if lane["cell_id"] == cell_id),
            key=lambda item: item["lane_id"],
        )
        if not cell_lanes:
            raise RuntimeError(f"Ground-Only policy leaves required cell empty: {cell_id}")
        cell_nodes = sorted(
            (node for node in nodes.values() if node["cell_id"] == cell_id),
            key=lambda item: item["node_id"],
        )
        cell_portals = sorted(
            (portal for portal in kept_portals if portal["local_cell_id"] == cell_id),
            key=lambda item: item["portal_id"],
        )
        component_ids = {node["component_id"] for node in cell_nodes}
        component_rows = [row for row in components if row["component_id"] in component_ids]
        cell = {
            "schema_version": parent_cell["schema_version"],
            "cell_id": cell_id,
            "grid_coordinate": parent_cell["grid_coordinate"],
            "world_bounds": parent_cell["world_bounds"],
            "source_feature_ids": sorted({lane["source_feature_id"] for lane in cell_lanes}),
            "nodes": cell_nodes,
            "directed_lanes": cell_lanes,
            "portals": cell_portals,
            "hashes": copy.deepcopy(parent_cell["hashes"]),
            "evidence": evidence(
                len(parent_cell["directed_lanes"]), cell_lanes, cell_portals,
                len(component_rows),
                sum(row["junction_count"] for row in component_rows),
                sum(row["street_block_count"] for row in component_rows), 1, 1,
            ),
            "certified": True,
        }
        cells.append(cell)

    components_by_id = {row["component_id"]: row for row in components}
    districts: list[dict[str, Any]] = []
    for cell in cells:
        cell_id = cell["cell_id"]
        candidates = sorted(
            {
                lane["component_id"]
                for lane in cell["directed_lanes"]
            },
            key=lambda identifier: (
                -components_by_id[identifier]["directional_lane_length_cm"], identifier
            ),
        )
        chosen = candidates[0]
        spawn_lanes = sorted(
            lane["lane_id"]
            for lane in cell["directed_lanes"]
            if lane["component_id"] == chosen
        )[:8]
        chosen_nodes = sorted(
            node["node_id"]
            for node in cell["nodes"]
            if node["component_id"] == chosen
        )
        districts.append({
            "district_id": "district-" + cell_id,
            "component_id": chosen,
            "cell_ids": [cell_id],
            "spawn_node_ids": chosen_nodes,
            "spawn_lane_ids": spawn_lanes,
            "world_bounds": cell["world_bounds"],
            "target_population": 50,
            "selection_weight": 1.0,
            "enabled": True,
        })

    excluded_lanes = [lane for lane in parent_lanes if lane["lane_id"] not in kept_lane_ids]
    excluded_by_feature: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for lane in excluded_lanes:
        excluded_by_feature[lane["source_feature_id"]].append(lane)
    excluded_features = []
    for feature_id, lanes in sorted(excluded_by_feature.items()):
        feature = features[feature_id]
        excluded_features.append({
            "source_feature_id": feature_id,
            "parent_feature_id": feature.get("parent_feature_id", feature_id),
            "reasons": decisions[feature_id],
            "directional_lane_count": len(lanes),
            "directional_lane_length_cm": round(sum(float(lane["length_cm"]) for lane in lanes), 4),
        })

    policy_hash = common.file_sha256(policy_path)
    parent_hash = common.file_sha256(parent_path)
    filter_record = {
        "policy_id": policy["policy_id"],
        "policy_sha256": policy_hash,
        "parent_path": "Scripts/OpenMassCrowd/CentralNetwork/Data/central_network_certified.json",
        "parent_file_sha256": parent_hash,
        "parent_build_id": parent["build_id"],
        "parent_combined_sha256": parent["hashes"]["combined_sha256"],
        "source_document_sha256": source["hashes"]["document_sha256"],
        "included_source_feature_ids": sorted({lane["source_feature_id"] for lane in kept_lanes}),
        "excluded_source_features": excluded_features,
        "included_directional_lane_count": len(kept_lanes),
        "excluded_directional_lane_count": len(excluded_lanes),
        "included_directional_lane_length_cm": round(sum(float(lane["length_cm"]) for lane in kept_lanes), 4),
        "excluded_directional_lane_length_cm": round(sum(float(lane["length_cm"]) for lane in excluded_lanes), 4),
    }

    result = {
        "$schema": parent.get("$schema", ""),
        "schema_version": parent["schema_version"],
        "network_id": "central-hong-kong-ground-only",
        "build_id": str(uuid.uuid5(uuid.NAMESPACE_URL, parent_hash + ":" + policy_hash)),
        "generator_version": "central-ground-only-" + SCRIPT_VERSION,
        "world_bounds": parent["world_bounds"],
        "hashes": copy.deepcopy(parent["hashes"]),
        "source_provenance": parent["source_provenance"],
        "ground_only_filter": filter_record,
        "cells": cells,
        "components": components,
        "spawn_districts": districts,
        "evidence": evidence(
            len(parent_lanes), kept_lanes, kept_portals, len(components),
            sum(row["junction_count"] for row in components),
            sum(row["street_block_count"] for row in components), len(districts), len(cells),
        ),
    }
    for cell in result["cells"]:
        content_hash, combined_hash = common.expected_cell_hashes(cell, result["hashes"])
        cell["hashes"]["cell_content_sha256"] = content_hash
        cell["hashes"]["combined_sha256"] = combined_hash
    root_content, root_combined = common.expected_root_hashes(result)
    result["hashes"]["cell_content_sha256"] = root_content
    result["hashes"]["combined_sha256"] = root_combined

    reason_counts = Counter(
        reason.split(":", 1)[0]
        for row in excluded_features
        for reason in row["reasons"]
    )
    audit = {
        "schema_version": 1,
        "generator_version": SCRIPT_VERSION,
        "status": "ok",
        "network_id": result["network_id"],
        "build_id": result["build_id"],
        "policy_id": policy["policy_id"],
        "policy_sha256": policy_hash,
        "parent_file_sha256": parent_hash,
        "parent_build_id": parent["build_id"],
        "parent_combined_sha256": parent["hashes"]["combined_sha256"],
        "included_source_feature_count": len(filter_record["included_source_feature_ids"]),
        "excluded_source_feature_count": len(excluded_features),
        "included_directional_lane_count": len(kept_lanes),
        "excluded_directional_lane_count": len(excluded_lanes),
        "included_directional_lane_length_cm": filter_record["included_directional_lane_length_cm"],
        "excluded_directional_lane_length_cm": filter_record["excluded_directional_lane_length_cm"],
        "component_count": len(components),
        "cell_count": len(cells),
        "spawn_district_count": len(districts),
        "target_population": sum(row["target_population"] for row in districts),
        "exclusion_reason_feature_counts": dict(sorted(reason_counts.items())),
        "component_directional_lengths_cm_desc": sorted(
            (row["directional_lane_length_cm"] for row in components), reverse=True
        ),
    }
    return result, audit


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--parent", type=Path, default=DEFAULT_PARENT)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--audit", type=Path, default=DEFAULT_AUDIT)
    args = parser.parse_args()
    parent = common.load_json_strict(args.parent)
    source = common.load_json_strict(args.source)
    policy = common.load_json_strict(args.policy)
    result, audit = derive(parent, source, policy, args.parent, args.policy)
    write_json(args.output, result)
    audit["output_file_sha256"] = common.file_sha256(args.output)
    write_json(args.audit, audit)
    print(json.dumps(audit, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
