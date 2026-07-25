#!/usr/bin/env python3
"""Audit whether disconnected certified lanes have deterministic OSM evidence.

This tool is deliberately read-only.  It distinguishes four very different
causes of apparent disconnection:

* inconsistent/re-keyed projected OSM nodes;
* true same-level geometric intersections which OSM did not node;
* rejected or review-gated OSM features; and
* strict Cesium certification failures which cut an otherwise connected graph.

Only an exact, same-level intersection between two independently certified
original OSM lanes with a <=5 cm certified height delta is reported as an
automatic recovery candidate.  Proximity-only endpoints, generated connectors,
different layers, bridges, tunnels, covered paths, and transition routes remain
review-only or rejected.  The audit never changes topology or trust state.
"""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter, defaultdict, deque
from pathlib import Path
from typing import Any, Iterable, Sequence

import central_osm_pipeline as osm_pipeline
import certify_central_network_with_cesium as certifier


SCRIPT_DIR = Path(__file__).resolve().parent
DATA_DIR = SCRIPT_DIR / "Data"
DEFAULT_SOURCE = DATA_DIR / "central_pedestrian_source.json"
DEFAULT_PROJECTED = DATA_DIR / "central_pedestrian_source_unreal.json"
DEFAULT_RAW = DATA_DIR / "central_osm_overpass_raw.json"
DEFAULT_CORRECTIONS = SCRIPT_DIR / "central_manual_corrections.json"
DEFAULT_WORK = DATA_DIR / "central_network_certification_working.json"

POINT_ID_POSITION_TOLERANCE_CM = 0.5
CERTIFIED_INTERSECTION_HEIGHT_TOLERANCE_CM = 5.0
REVIEW_ONLY_NEAR_ENDPOINT_BANDS_CM = (20.0, 55.0, 100.0)


def _distance_xy(first: Sequence[float], second: Sequence[float]) -> float:
    return math.hypot(float(second[0]) - float(first[0]), float(second[1]) - float(first[1]))


def _component_index(
    features: Sequence[dict[str, Any]],
) -> tuple[list[set[str]], dict[str, int]]:
    adjacency: dict[str, set[str]] = defaultdict(set)
    for feature in features:
        point_ids = [str(point["point_id"]) for point in feature.get("points", [])]
        for point_id in point_ids:
            adjacency.setdefault(point_id, set())
        for first, second in zip(point_ids, point_ids[1:]):
            if first != second:
                adjacency[first].add(second)
                adjacency[second].add(first)

    remaining = set(adjacency)
    components: list[set[str]] = []
    node_component: dict[str, int] = {}
    while remaining:
        start = min(remaining)
        remaining.remove(start)
        nodes = {start}
        queue = deque([start])
        while queue:
            current = queue.popleft()
            for neighbor in adjacency[current]:
                if neighbor in remaining:
                    remaining.remove(neighbor)
                    nodes.add(neighbor)
                    queue.append(neighbor)
        component_id = len(components)
        components.append(nodes)
        for node_id in nodes:
            node_component[node_id] = component_id
    return components, node_component


def _admitted_features(
    projected: dict[str, Any], corrections: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    accepted_reviews = certifier.accepted_manual_review_ids(corrections)
    explicit_exclusions = certifier.excluded_manual_review_ids(corrections)
    admitted: list[dict[str, Any]] = []
    review_blocked: list[str] = []
    excluded: list[str] = []
    for feature in sorted(projected.get("features", []), key=lambda item: item["feature_id"]):
        feature_id = str(feature["feature_id"])
        if feature_id in explicit_exclusions:
            excluded.append(feature_id)
        elif feature.get("requires_manual_review", False) and feature_id not in accepted_reviews:
            review_blocked.append(feature_id)
        elif len(feature.get("points", [])) >= 2:
            admitted.append(feature)
    return admitted, review_blocked, excluded


def _strict_structure_profile(value: dict[str, Any]) -> dict[str, Any]:
    candidate = {
        "classification": value.get("classification", "footway"),
        "tags": value.get("tags", {}),
    }
    profile = certifier.semantic_profile(candidate)
    tags = candidate["tags"]
    tunnel = str(tags.get("tunnel", "")).lower() not in {"", "no", "false", "0"}
    indoor = str(tags.get("indoor", "")).lower() == "yes"
    profile["tunnel"] = tunnel
    profile["indoor"] = indoor
    profile["strict_ground"] = bool(
        profile["level_key"] == "ground-layer-0"
        and not profile["explicit_layer"]
        and not tunnel
        and not indoor
    )
    return profile


def _line_intersection(
    first_start: Sequence[float],
    first_end: Sequence[float],
    second_start: Sequence[float],
    second_end: Sequence[float],
) -> tuple[float, float, list[float]] | None:
    ax, ay = float(first_start[0]), float(first_start[1])
    bx, by = float(first_end[0]), float(first_end[1])
    cx, cy = float(second_start[0]), float(second_start[1])
    dx, dy = float(second_end[0]), float(second_end[1])
    rx, ry = bx - ax, by - ay
    sx, sy = dx - cx, dy - cy
    denominator = rx * sy - ry * sx
    scale = max(1.0, math.hypot(rx, ry) * math.hypot(sx, sy))
    if abs(denominator) <= scale * 1.0e-12:
        return None
    qx, qy = cx - ax, cy - ay
    first_fraction = (qx * sy - qy * sx) / denominator
    second_fraction = (qx * ry - qy * rx) / denominator
    epsilon = 1.0e-9
    if not (
        -epsilon <= first_fraction <= 1.0 + epsilon
        and -epsilon <= second_fraction <= 1.0 + epsilon
    ):
        return None
    first_fraction = min(1.0, max(0.0, first_fraction))
    second_fraction = min(1.0, max(0.0, second_fraction))
    return (
        first_fraction,
        second_fraction,
        [ax + first_fraction * rx, ay + first_fraction * ry],
    )


def _feature_segments(
    features: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    segments: list[dict[str, Any]] = []
    for feature in features:
        profile = _strict_structure_profile(feature)
        for segment_index, (first, second) in enumerate(
            zip(feature.get("points", []), feature.get("points", [])[1:])
        ):
            segments.append(
                {
                    "feature": feature,
                    "segment_index": segment_index,
                    "first": first,
                    "second": second,
                    "profile": profile,
                }
            )
    return segments


def source_intersection_audit(
    features: Sequence[dict[str, Any]], node_component: dict[str, int]
) -> dict[str, Any]:
    segments = _feature_segments(features)
    cross_component: list[dict[str, Any]] = []
    for first_index, first in enumerate(segments):
        first_feature = first["feature"]
        first_points = {first["first"]["point_id"], first["second"]["point_id"]}
        first_component = node_component[str(first["first"]["point_id"])]
        for second in segments[first_index + 1 :]:
            second_feature = second["feature"]
            if first_feature["parent_feature_id"] == second_feature["parent_feature_id"]:
                continue
            second_points = {second["first"]["point_id"], second["second"]["point_id"]}
            if first_points.intersection(second_points):
                continue
            second_component = node_component[str(second["first"]["point_id"])]
            if first_component == second_component:
                continue
            intersection = _line_intersection(
                first["first"]["unreal_position_cm"],
                first["second"]["unreal_position_cm"],
                second["first"]["unreal_position_cm"],
                second["second"]["unreal_position_cm"],
            )
            if intersection is None:
                continue
            compatible = bool(
                first["profile"]["strict_ground"]
                and second["profile"]["strict_ground"]
            )
            cross_component.append(
                {
                    "first_feature_id": first_feature["feature_id"],
                    "second_feature_id": second_feature["feature_id"],
                    "first_component": first_component,
                    "second_component": second_component,
                    "first_level": first["profile"]["level_key"],
                    "second_level": second["profile"]["level_key"],
                    "same_level_ground": compatible,
                    "xy_cm": [round(value, 4) for value in intersection[2]],
                }
            )
    cross_component.sort(
        key=lambda item: (item["first_feature_id"], item["second_feature_id"], item["xy_cm"])
    )
    return {
        "cross_component_non_noded_intersection_count": len(cross_component),
        "same_level_ground_intersection_count": sum(
            item["same_level_ground"] for item in cross_component
        ),
        "different_structure_or_level_count": sum(
            not item["same_level_ground"] for item in cross_component
        ),
        "examples": cross_component[:24],
    }


def projected_point_id_audit(
    features: Sequence[dict[str, Any]], node_component: dict[str, int]
) -> dict[str, Any]:
    observations: dict[str, list[list[float]]] = defaultdict(list)
    for feature in features:
        for point in feature.get("points", []):
            observations[str(point["point_id"])].append(
                [float(value) for value in point["unreal_position_cm"]]
            )

    inconsistent: list[dict[str, Any]] = []
    positions: list[tuple[str, list[float]]] = []
    for point_id, values in sorted(observations.items()):
        reference = values[0]
        maximum_delta = max((_distance_xy(reference, value) for value in values[1:]), default=0.0)
        if maximum_delta > POINT_ID_POSITION_TOLERANCE_CM:
            inconsistent.append(
                {
                    "point_id": point_id,
                    "maximum_xy_delta_cm": round(maximum_delta, 6),
                    "observation_count": len(values),
                }
            )
        positions.append((point_id, reference))

    aliases: list[dict[str, Any]] = []
    near_cross_component = Counter()
    for first_index, (first_id, first_position) in enumerate(positions):
        for second_id, second_position in positions[first_index + 1 :]:
            if node_component.get(first_id) == node_component.get(second_id):
                continue
            distance = _distance_xy(first_position, second_position)
            if distance <= POINT_ID_POSITION_TOLERANCE_CM:
                aliases.append(
                    {
                        "first_point_id": first_id,
                        "second_point_id": second_id,
                        "xy_distance_cm": round(distance, 6),
                        "source_z_delta_cm": round(
                            abs(first_position[2] - second_position[2]), 6
                        ),
                    }
                )
            for band in REVIEW_ONLY_NEAR_ENDPOINT_BANDS_CM:
                if distance <= band:
                    near_cross_component[str(int(band))] += 1
    return {
        "unique_point_id_count": len(observations),
        "reused_point_id_count": sum(len(values) > 1 for values in observations.values()),
        "inconsistent_shared_point_id_count": len(inconsistent),
        "inconsistent_shared_point_ids": inconsistent[:24],
        "strict_coordinate_alias_count": len(aliases),
        "strict_coordinate_aliases": aliases[:24],
        "review_only_cross_component_near_point_pairs": {
            f"within_{int(band)}cm": int(near_cross_component[str(int(band))])
            for band in REVIEW_ONLY_NEAR_ENDPOINT_BANDS_CM
        },
        "position_tolerance_cm": POINT_ID_POSITION_TOLERANCE_CM,
    }


def filtered_feature_bridge_audit(
    raw: dict[str, Any], node_component: dict[str, int]
) -> dict[str, Any]:
    bridges: list[dict[str, Any]] = []
    reasons = Counter()
    for way in sorted(
        (element for element in raw.get("elements", []) if element.get("type") == "way"),
        key=lambda item: int(item.get("id", 0)),
    ):
        tags = osm_pipeline.normalise_tags(way.get("tags"))
        admission = osm_pipeline.classify_way(tags)
        if admission.accepted:
            continue
        touched: dict[int, list[str]] = defaultdict(list)
        for raw_node_id in way.get("nodes", []):
            point_id = f"osm-node-{int(raw_node_id)}"
            if point_id in node_component:
                touched[node_component[point_id]].append(point_id)
        if len(touched) < 2:
            continue
        reasons[admission.rule] += 1
        bridges.append(
            {
                "parent_feature_id": f"osm-way-{int(way['id'])}",
                "rejection_reason": admission.rule,
                "touched_source_components": sorted(touched),
                "highway": tags.get("highway"),
                "indoor": tags.get("indoor"),
                "bridge": tags.get("bridge"),
                "tunnel": tags.get("tunnel"),
                "layer": tags.get("layer"),
            }
        )
    return {
        "rejected_feature_bridge_count": len(bridges),
        "reason_counts": dict(sorted(reasons.items())),
        "automatic_admission_count": 0,
        "examples": bridges[:24],
    }


def _sample_z_at_fraction(
    candidate: dict[str, Any], result: dict[str, Any], fraction: float
) -> float:
    samples = certifier.orient_result_to_candidate(candidate, result)
    positions = [sample["center_position"] for sample in samples]
    if len(positions) == 1:
        return float(positions[0][2])
    lengths = [_distance_xy(first, second) for first, second in zip(positions, positions[1:])]
    total = sum(lengths)
    target = min(1.0, max(0.0, fraction)) * total
    accumulated = 0.0
    for first, second, length in zip(positions, positions[1:], lengths):
        if length > 0.0 and accumulated + length >= target:
            alpha = (target - accumulated) / length
            return float(first[2]) + alpha * (float(second[2]) - float(first[2]))
        accumulated += length
    return float(positions[-1][2])


def certified_intersection_recovery_candidates(
    plan: dict[str, Any], work: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    trusted_ids = certifier.trusted_runtime_result_ids(plan, work["results"])
    components = certifier.accepted_components(
        plan,
        work["results"],
        allowed_origins=certifier.TRUSTED_RUNTIME_TOPOLOGY_ORIGINS,
        allowed_candidate_ids=trusted_ids,
    )
    component_by_candidate = {
        candidate["candidate_id"]: component_index
        for component_index, component in enumerate(components)
        for candidate, _result in component["pairs"]
    }
    candidate_by_id = {
        candidate["candidate_id"]: candidate for candidate in plan["candidates"]
    }
    original_pairs = [
        (candidate_by_id[candidate_id], work["results"][candidate_id])
        for candidate_id in sorted(trusted_ids)
        if candidate_by_id[candidate_id].get("topology_origin") == "openstreetmap"
    ]
    recoveries: list[dict[str, Any]] = []
    rejected_crossings: list[dict[str, Any]] = []
    for first_index, (first, first_result) in enumerate(original_pairs):
        first_nodes = {first["from_point_id"], first["to_point_id"]}
        first_component = component_by_candidate[first["candidate_id"]]
        first_profile = _strict_structure_profile(first)
        for second, second_result in original_pairs[first_index + 1 :]:
            second_component = component_by_candidate[second["candidate_id"]]
            if first_component == second_component:
                continue
            if first_nodes.intersection({second["from_point_id"], second["to_point_id"]}):
                continue
            if first.get("source_feature_id") == second.get("source_feature_id"):
                continue
            intersection = _line_intersection(
                first["from_source_position"],
                first["to_source_position"],
                second["from_source_position"],
                second["to_source_position"],
            )
            if intersection is None:
                continue
            second_profile = _strict_structure_profile(second)
            height_delta = abs(
                _sample_z_at_fraction(first, first_result, intersection[0])
                - _sample_z_at_fraction(second, second_result, intersection[1])
            )
            record = {
                "first_candidate_id": first["candidate_id"],
                "second_candidate_id": second["candidate_id"],
                "first_component": first_component,
                "second_component": second_component,
                "certified_height_delta_cm": round(height_delta, 6),
                "xy_cm": [round(value, 4) for value in intersection[2]],
            }
            if (
                first_profile["strict_ground"]
                and second_profile["strict_ground"]
                and height_delta <= CERTIFIED_INTERSECTION_HEIGHT_TOLERANCE_CM
            ):
                recoveries.append(record)
            else:
                record["first_level"] = first_profile["level_key"]
                record["second_level"] = second_profile["level_key"]
                rejected_crossings.append(record)
    return recoveries, rejected_crossings


def build_audit(
    *,
    source: dict[str, Any],
    projected: dict[str, Any],
    raw: dict[str, Any],
    corrections: dict[str, Any],
    work: dict[str, Any],
) -> dict[str, Any]:
    admitted, review_blocked, explicitly_excluded = _admitted_features(projected, corrections)
    source_components, node_component = _component_index(admitted)
    point_ids = {point_id for component in source_components for point_id in component}
    source_length_cm = sum(
        _distance_xy(first["unreal_position_cm"], second["unreal_position_cm"])
        for feature in admitted
        for first, second in zip(feature["points"], feature["points"][1:])
    )

    plan = certifier.build_source_plan(projected, corrections)
    original_candidate_count = len(plan["candidates"])
    plan["candidates"].extend(work.get("generated_connector_candidates", []))
    semantic_candidates = work.get("semantic_recovery_candidates", [])
    certifier._register_semantic_nodes(plan, semantic_candidates)
    plan["candidates"].extend(semantic_candidates)
    trusted_ids = certifier.trusted_runtime_result_ids(plan, work["results"])
    trusted_components = certifier.accepted_components(
        plan,
        work["results"],
        allowed_origins=certifier.TRUSTED_RUNTIME_TOPOLOGY_ORIGINS,
        allowed_candidate_ids=trusted_ids,
    )
    recoveries, rejected_certified_crossings = certified_intersection_recovery_candidates(
        plan, work
    )
    original_ids = {
        candidate["candidate_id"]
        for candidate in plan["candidates"][:original_candidate_count]
    }
    original_status = Counter()
    original_rejection_reasons = Counter()
    for candidate_id in sorted(original_ids):
        result = work["results"].get(candidate_id, {})
        status = str(result.get("status", "missing"))
        original_status[status] += 1
        if status == "rejected":
            original_rejection_reasons[str(result.get("reason", "unknown"))] += 1

    cut = certifier.semantic_recovery_plan(plan, work["results"])
    point_audit = projected_point_id_audit(admitted, node_component)
    source_intersections = source_intersection_audit(admitted, node_component)
    filtered_bridges = filtered_feature_bridge_audit(raw, node_component)
    trusted_length_cm = sum(component["length_cm"] for component in trusted_components)
    # A coordinate-near point pair is evidence for review, not proof that two
    # independently identified OSM nodes are the same pedestrian junction.
    safe_recovery_count = len(recoveries)
    dominant_cause = "cesium-certification-rejections"
    if point_audit["inconsistent_shared_point_id_count"]:
        dominant_cause = "projected-shared-point-id-inconsistency"
    elif point_audit["strict_coordinate_alias_count"]:
        dominant_cause = "projected-point-id-aliasing"
    elif recoveries:
        dominant_cause = "certified-same-level-non-noded-intersections"

    return {
        "status": "recovery-evidence-found" if safe_recovery_count else "no-safe-automatic-recovery",
        "policy": {
            "point_id_position_tolerance_cm": POINT_ID_POSITION_TOLERANCE_CM,
            "certified_intersection_height_tolerance_cm": CERTIFIED_INTERSECTION_HEIGHT_TOLERANCE_CM,
            "proximity_only_is_trusted": False,
            "generated_connectors_are_trusted": False,
            "cross_layer_intersections_are_trusted": False,
        },
        "source_topology": {
            "topology_sha256": source.get("hashes", {}).get("topology_sha256"),
            "declared_component_count_before_review_gate": source.get("audit", {}).get(
                "coarse_connected_component_count"
            ),
            "declared_total_source_length_m_before_review_gate": source.get("audit", {}).get(
                "total_source_length_m"
            ),
            "admitted_feature_count": len(admitted),
            "admitted_point_id_count": len(point_ids),
            "component_count": len(source_components),
            "total_directional_length_cm": round(source_length_cm * 2.0, 3),
            "primary_component": plan["primary_component"],
            "review_blocked_feature_count": len(review_blocked),
            "review_blocked_feature_ids": review_blocked,
            "explicitly_excluded_feature_count": len(explicitly_excluded),
        },
        "projected_point_ids": point_audit,
        "source_geometric_intersections": source_intersections,
        "filtered_feature_bridges": filtered_bridges,
        "certification_fragmentation": {
            "original_candidate_count": original_candidate_count,
            "original_status_counts": dict(sorted(original_status.items())),
            "original_rejection_reason_counts": dict(
                sorted(original_rejection_reasons.items())
            ),
            "trusted_accepted_candidate_count": len(trusted_ids),
            "trusted_component_count": len(trusted_components),
            "trusted_total_directional_length_cm": round(trusted_length_cm * 2.0, 3),
            "semantic_connectivity_cut_feasible": bool(cut.get("feasible")),
            "semantic_connectivity_cut_edge_count": int(
                cut.get("rejected_edge_count", 0) or 0
            ),
            "semantic_connectivity_cut_candidate_ids": list(
                cut.get("connectivity_cut_candidate_ids", cut.get("cut_candidate_ids", []))
            ),
            "semantic_length_expansion_candidate_count": len(
                cut.get("length_expansion_candidate_ids", [])
            ),
            "semantic_full_recovery_candidate_count": len(
                cut.get("cut_candidate_ids", [])
            ),
        },
        "certified_intersection_recovery": {
            "eligible_count": len(recoveries),
            "eligible": recoveries[:24],
            "rejected_cross_layer_or_height_count": len(rejected_certified_crossings),
            "rejected_examples": rejected_certified_crossings[:24],
        },
        "conclusion": {
            "dominant_disconnection_cause": dominant_cause,
            "safe_automatic_recovery_count": safe_recovery_count,
            "manual_or_candidate_links_still_require_explicit_review": True,
            "promotion_allowed_by_this_audit": False,
        },
    }


def _accepted_result(
    candidate: dict[str, Any], first_z: float, second_z: float
) -> dict[str, Any]:
    return {
        "status": "accepted",
        "topology_origin": candidate["topology_origin"],
        "from_point_id": candidate["from_point_id"],
        "to_point_id": candidate["to_point_id"],
        "samples": [
            {"center_position": candidate["from_source_position"][:2] + [first_z]},
            {"center_position": candidate["to_source_position"][:2] + [second_z]},
        ],
    }


def run_self_test() -> dict[str, Any]:
    feature_a = {
        "feature_id": "osm-way-a-cell-part-000",
        "parent_feature_id": "osm-way-a",
        "classification": "sidewalk",
        "tags": {"highway": "footway", "footway": "sidewalk"},
        "points": [
            {"point_id": "a0", "unreal_position_cm": [0.0, 0.0, 0.0]},
            {"point_id": "a1", "unreal_position_cm": [10.0, 0.0, 0.0]},
        ],
    }
    feature_b = {
        "feature_id": "osm-way-b-cell-part-000",
        "parent_feature_id": "osm-way-b",
        "classification": "crossing",
        "tags": {"highway": "footway", "footway": "crossing"},
        "points": [
            {"point_id": "b0", "unreal_position_cm": [5.0, -5.0, 0.0]},
            {"point_id": "b1", "unreal_position_cm": [5.0, 5.0, 0.0]},
        ],
    }
    components, node_component = _component_index([feature_a, feature_b])
    intersection = source_intersection_audit([feature_a, feature_b], node_component)
    if len(components) != 2 or intersection["same_level_ground_intersection_count"] != 1:
        raise RuntimeError("same-level non-noded source intersection was not discovered")

    bridge = json.loads(json.dumps(feature_b))
    bridge["tags"] = {"highway": "footway", "bridge": "yes", "layer": "1"}
    _components, bridge_index = _component_index([feature_a, bridge])
    bridge_audit = source_intersection_audit([feature_a, bridge], bridge_index)
    if bridge_audit["same_level_ground_intersection_count"] != 0:
        raise RuntimeError("cross-layer intersection was incorrectly trusted")

    candidates = [
        {
            "candidate_id": "candidate-a",
            "source_feature_id": feature_a["feature_id"],
            "source_cell_id": "central-r0-c0",
            "from_cell_id": "central-r0-c0",
            "to_cell_id": "central-r0-c0",
            "classification": "sidewalk",
            "topology_origin": "openstreetmap",
            "from_point_id": "a0",
            "to_point_id": "a1",
            "from_source_position": [0.0, 0.0, 0.0],
            "to_source_position": [10.0, 0.0, 0.0],
            "source_length_xy_cm": 10.0,
            "tags": feature_a["tags"],
        },
        {
            "candidate_id": "candidate-b",
            "source_feature_id": feature_b["feature_id"],
            "source_cell_id": "central-r0-c1",
            "from_cell_id": "central-r0-c1",
            "to_cell_id": "central-r0-c1",
            "classification": "crossing",
            "topology_origin": "openstreetmap",
            "from_point_id": "b0",
            "to_point_id": "b1",
            "from_source_position": [5.0, -5.0, 0.0],
            "to_source_position": [5.0, 5.0, 0.0],
            "source_length_xy_cm": 10.0,
            "tags": feature_b["tags"],
        },
    ]
    plan = {
        "candidates": candidates,
        "node_owner": {"a0": "central-r0-c0", "a1": "central-r0-c0", "b0": "central-r0-c1", "b1": "central-r0-c1"},
    }
    work = {
        "results": {
            "candidate-a": _accepted_result(candidates[0], 100.0, 100.0),
            "candidate-b": _accepted_result(candidates[1], 103.0, 103.0),
        }
    }
    recoveries, _rejected = certified_intersection_recovery_candidates(plan, work)
    if len(recoveries) != 1:
        raise RuntimeError("certified same-height intersection was not eligible")
    work["results"]["candidate-b"] = _accepted_result(candidates[1], 200.0, 200.0)
    recoveries, rejected = certified_intersection_recovery_candidates(plan, work)
    if recoveries or len(rejected) != 1:
        raise RuntimeError("certified height mismatch did not fail closed")
    return {
        "status": "ok",
        "same_level_source_intersection": True,
        "cross_layer_failed_closed": True,
        "certified_height_match_eligible": True,
        "certified_height_mismatch_failed_closed": True,
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--projected", type=Path, default=DEFAULT_PROJECTED)
    parser.add_argument("--raw", type=Path, default=DEFAULT_RAW)
    parser.add_argument("--corrections", type=Path, default=DEFAULT_CORRECTIONS)
    parser.add_argument("--work", type=Path, default=DEFAULT_WORK)
    parser.add_argument("--self-test", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.self_test:
        print(json.dumps(run_self_test(), indent=2, sort_keys=True))
        return 0
    report = build_audit(
        source=certifier.read_json(args.source),
        projected=certifier.read_json(args.projected),
        raw=certifier.read_json(args.raw),
        corrections=certifier.read_json(args.corrections),
        work=certifier.read_json(args.work),
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
