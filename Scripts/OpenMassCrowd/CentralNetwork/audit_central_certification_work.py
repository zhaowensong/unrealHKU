#!/usr/bin/env python3
"""Summarize connectivity and rejection evidence in the live work cache."""

from __future__ import annotations

import json
from collections import Counter

import certify_central_network_with_cesium as certifier


def main() -> None:
    projected = certifier.read_json(certifier.PROJECTED_SOURCE_PATH)
    corrections = certifier.read_json(certifier.CORRECTIONS_PATH)
    plan = certifier.build_source_plan(projected, corrections)
    work = certifier.read_json(certifier.WORK_PATH)
    plan["candidates"].extend(work.get("generated_connector_candidates", []))
    semantic_candidates = work.get("semantic_recovery_candidates", [])
    certifier._register_semantic_nodes(plan, semantic_candidates)
    plan["candidates"].extend(semantic_candidates)
    candidate_by_id = {
        candidate["candidate_id"]: candidate for candidate in plan["candidates"]
    }
    physical_components = certifier.accepted_components(plan, work["results"])
    trusted_result_ids = certifier.trusted_runtime_result_ids(
        plan, work["results"]
    )
    components = certifier.accepted_components(
        plan,
        work["results"],
        allowed_origins=certifier.RUNTIME_ELIGIBLE_TOPOLOGY_ORIGINS,
        allowed_candidate_ids=trusted_result_ids,
    )
    component_coverages = [
        certifier.certified_component_coverage(component)
        for component in components
    ]
    required_cells = sorted(plan["cell_ids"])
    for coverage in component_coverages:
        coverage["passes_main_component_gate"] = bool(
            coverage["cell_ids"] == required_cells
            and coverage["directional_lane_length_cm"]
            >= certifier.MINIMUM_TRUSTED_UNDIRECTED_LENGTH_CM * 2.0
            and coverage["street_block_count"]
            >= certifier.MINIMUM_CONNECTED_STREET_BLOCKS
            and coverage["junction_count"]
            >= certifier.MINIMUM_CERTIFIED_JUNCTIONS
        )
        coverage.pop("candidate_ids", None)
    component_coverages.sort(
        key=lambda coverage: (
            not coverage["passes_main_component_gate"],
            -len(coverage["cell_ids"]),
            -coverage["directional_lane_length_cm"],
            -coverage["street_block_count"],
            -coverage["junction_count"],
            coverage["cell_ids"],
        )
    )
    component_positions = [
        certifier.canonical_node_positions(component["pairs"])
        for component in components
    ]
    nearest_component_pairs = []
    for first_index, first_component in enumerate(components):
        first_positions = component_positions[first_index]
        for second_index in range(first_index + 1, len(components)):
            second_component = components[second_index]
            second_positions = component_positions[second_index]
            best = None
            for first_node, first_position in first_positions.items():
                for second_node, second_position in second_positions.items():
                    distance = certifier.distance_xy(first_position, second_position)
                    if best is None or distance < best[0]:
                        best = (distance, first_node, second_node)
            if best is not None:
                nearest_component_pairs.append(
                    {
                        "first_component": first_index,
                        "second_component": second_index,
                        "first_cells": first_component["cells"],
                        "second_cells": second_component["cells"],
                        "distance_cm": round(best[0], 3),
                        "first_node": best[1],
                        "second_node": best[2],
                    }
                )
    nearest_component_pairs.sort(key=lambda item: item["distance_cm"])
    accepted_by_cell = Counter()
    rejected_by_cell_reason = Counter()
    generated_by_round_status = Counter()
    semantic_by_round_status = Counter()
    semantic_failure_by_source_reason = Counter()
    semantic_failure_evidence_count = 0
    for candidate_id, result in work["results"].items():
        candidate = candidate_by_id[candidate_id]
        cell_id = candidate["source_cell_id"]
        if result["status"] == "accepted":
            accepted_by_cell[cell_id] += 1
        else:
            rejected_by_cell_reason[(cell_id, result["reason"])] += 1
        if candidate.get("topology_origin") == "generated-connector":
            round_number = int(candidate.get("tags", {}).get("round", 0))
            generated_by_round_status[
                (round_number, result["status"], result.get("reason", ""))
            ] += 1
        if candidate.get("topology_origin") == "osm-semantic-recovery":
            round_number = int(candidate.get("tags", {}).get("semantic_round", 0))
            semantic_by_round_status[
                (round_number, result["status"], result.get("reason", ""))
            ] += 1
            if result.get("failure_evidence"):
                semantic_failure_evidence_count += 1
            if result["status"] == "rejected":
                semantic_failure_by_source_reason[
                    (
                        candidate["semantic_source_candidate_id"],
                        result.get("reason", "unknown"),
                    )
                ] += 1

    semantic_cut = certifier.semantic_recovery_plan(plan, work["results"])
    semantic_attempts = certifier.semantic_recovery_attempt_state(
        plan, work["results"]
    )
    base_alternative = certifier.semantic_recovery_plan(
        plan,
        work["results"],
        blocked_directions=semantic_attempts["base_blocked_directions"],
    )
    expanded_alternative = certifier.semantic_recovery_plan(
        plan,
        work["results"],
        blocked_directions=semantic_attempts["fully_blocked_directions"],
    )
    trusted_nodes = {
        node_id
        for candidate_id in trusted_result_ids
        for node_id in (
            candidate_by_id[candidate_id]["from_point_id"],
            candidate_by_id[candidate_id]["to_point_id"],
        )
    }
    direction_summary_by_key = {
        (
            summary["source_candidate_id"],
            summary["start_point_id"],
        ): summary
        for summary in semantic_attempts["direction_summaries"]
    }
    current_connectivity_frontiers = []
    for source_id in semantic_cut.get(
        "connectivity_cut_candidate_ids", semantic_cut.get("cut_candidate_ids", [])
    ):
        source = candidate_by_id[source_id]
        for start_point_id in (source["from_point_id"], source["to_point_id"]):
            if start_point_id not in trusted_nodes:
                continue
            profile = certifier.semantic_profile(source)
            summary = direction_summary_by_key.get((source_id, start_point_id), {})
            current_connectivity_frontiers.append(
                {
                    "source_candidate_id": source_id,
                    "start_point_id": start_point_id,
                    "original_reason": work["results"][source_id].get("reason"),
                    "semantic_level_key": profile["level_key"],
                    "ground_sidewalk": profile["ground_sidewalk"],
                    "explicit_layer": profile["explicit_layer"],
                    "base_exhausted": bool(summary.get("base_exhausted")),
                    "fully_exhausted": bool(summary.get("fully_exhausted")),
                    "expansion_eligible": bool(
                        summary.get("expansion_eligible")
                    ),
                    "attempted_offsets": summary.get("attempted_offsets", {}),
                    "next_base_offsets": list(
                        certifier.semantic_offsets_for_next_attempt(
                            source,
                            start_point_id,
                            semantic_attempts,
                            allow_expansion=False,
                        )
                    ),
                    "next_expanded_offsets": list(
                        certifier.semantic_offsets_for_next_attempt(
                            source,
                            start_point_id,
                            semantic_attempts,
                            allow_expansion=True,
                        )
                    ),
                    "semantic_failure_reasons": {
                        reason: count
                        for (candidate_id, reason), count in sorted(
                            semantic_failure_by_source_reason.items()
                        )
                        if candidate_id == source_id
                    },
                }
            )
    accepted_generated_ids = [
        candidate_id
        for candidate_id, result in work["results"].items()
        if result.get("status") == "accepted"
        and candidate_by_id[candidate_id].get("topology_origin")
        == "generated-connector"
    ]
    accepted_generated_length_cm = sum(
        certifier.polyline_length(work["results"][candidate_id]["samples"])
        for candidate_id in accepted_generated_ids
    )

    temporary_status = {"exists": False}
    temporary_path = certifier.WORK_PATH.with_name(certifier.WORK_PATH.name + ".tmp")
    if temporary_path.exists():
        try:
            temporary_work = certifier.read_json(temporary_path)
            temporary_status = {
                "exists": True,
                "valid_json": True,
                "compatible": temporary_work.get("compatibility")
                == work.get("compatibility"),
                "resolved": len(temporary_work.get("results", {})),
                "updated_at_utc": temporary_work.get("updated_at_utc"),
            }
        except (OSError, ValueError) as error:
            temporary_status = {
                "exists": True,
                "valid_json": False,
                "error": str(error),
            }

    report = {
        "final_cache_exists": certifier.FINAL_PATH.exists(),
        "work_complete": bool(work.get("complete")),
        "expected": len(plan["candidates"]),
        "resolved": len(work["results"]),
        "pending": len(plan["candidates"]) - len(work["results"]),
        "accepted": sum(accepted_by_cell.values()),
        "accepted_by_cell": dict(sorted(accepted_by_cell.items())),
        "rejected_by_cell_reason": {
            "{}:{}".format(cell_id, reason): count
            for (cell_id, reason), count in sorted(rejected_by_cell_reason.items())
        },
        "accepted_component_count": len(components),
        "physical_accepted_component_count": len(physical_components),
        "six_cell_component_count": sum(
            len(component["cells"]) == certifier.CELL_COUNT
            for component in components
        ),
        "accepted_component_total_length_cm": round(
            sum(component["length_cm"] for component in components), 3
        ),
        "component_cell_span_counts": dict(
            sorted(Counter(len(component["cells"]) for component in components).items())
        ),
        "main_component_gate": {
            "passed": any(
                coverage["passes_main_component_gate"]
                for coverage in component_coverages
            ),
            "qualifying_component_count": sum(
                coverage["passes_main_component_gate"]
                for coverage in component_coverages
            ),
            "required_cell_ids": required_cells,
            "minimum_directional_lane_length_cm": (
                certifier.MINIMUM_TRUSTED_UNDIRECTED_LENGTH_CM * 2.0
            ),
            "minimum_connected_street_blocks": (
                certifier.MINIMUM_CONNECTED_STREET_BLOCKS
            ),
            "minimum_junctions": certifier.MINIMUM_CERTIFIED_JUNCTIONS,
            "best_components": component_coverages[:8],
        },
        "trusted_runtime_topology": {
            "allowed_origins": sorted(
                certifier.RUNTIME_ELIGIBLE_TOPOLOGY_ORIGINS
            ),
            "selected_candidate_count": len(trusted_result_ids),
            "minimum_undirected_length_cm": (
                certifier.MINIMUM_TRUSTED_UNDIRECTED_LENGTH_CM
            ),
            "generated_connector_contribution": bool(
                set(trusted_result_ids) & set(accepted_generated_ids)
            ),
            "accepted_generated_connector_count_excluded": len(
                accepted_generated_ids
            ),
            "accepted_generated_connector_length_cm_excluded": round(
                accepted_generated_length_cm, 3
            ),
        },
        "generated_connector_rounds": work.get("generated_connector_rounds", []),
        "generated_connector_progress": {
            "round-{}:{}:{}".format(round_number, status, reason or "none"): count
            for (round_number, status, reason), count in sorted(
                generated_by_round_status.items()
            )
        },
        "semantic_recovery_rounds": work.get("semantic_recovery_rounds", []),
        "semantic_recovery_progress": {
            "round-{}:{}:{}".format(round_number, status, reason or "none"): count
            for (round_number, status, reason), count in sorted(
                semantic_by_round_status.items()
            )
        },
        "semantic_failure_evidence_count": semantic_failure_evidence_count,
        "semantic_recovery_cut": semantic_cut,
        "semantic_recovery_attempt_state": {
            "attempted_variant_count": len(
                semantic_attempts["attempted_variants"]
            ),
            "complete_source_count": len(
                semantic_attempts["complete_source_ids"]
            ),
            "base_blocked_direction_count": len(
                semantic_attempts["base_blocked_directions"]
            ),
            "fully_blocked_direction_count": len(
                semantic_attempts["fully_blocked_directions"]
            ),
            "expansion_eligible_direction_count": len(
                semantic_attempts["expansion_eligible_directions"]
            ),
            "base_alternative_cut_feasible": bool(
                base_alternative.get("feasible")
            ),
            "expanded_alternative_cut_feasible": bool(
                expanded_alternative.get("feasible")
            ),
        },
        "current_connectivity_frontiers": current_connectivity_frontiers,
        "temporary_checkpoint": temporary_status,
        "top_components": [
            {
                "cells": component["cells"],
                "node_count": len(component["nodes"]),
                "candidate_count": len(component["pairs"]),
                "length_cm": round(component["length_cm"], 3),
            }
            for component in components[:12]
        ],
        "nearest_component_pairs": nearest_component_pairs[:24],
    }
    print("CENTRAL_CESIUM_WORK_AUDIT=" + json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
