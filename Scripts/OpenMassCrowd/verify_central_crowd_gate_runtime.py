"""Asynchronously verify one staged Hong Kong Central crowd gate in PIE.

Run this file through ``run_unreal_python_via_mcp.py`` while PIE is already
running.  The verifier only observes the existing PIE session: it never starts,
stops, pauses, or changes the population gate.

The requested gate can be forwarded as ``--gate`` through the MCP runner, or
supplied as ``OPEN_MASS_CENTRAL_GATE`` (30, 100, 200, or 300) in the Editor
process. When neither is present, the verifier infers the gate from the sole
runtime spawner. Every promotion gate is sampled for at least 60 seconds so a
short clean window cannot hide a later routing or overlap failure::

    python Scripts/OpenMassCrowd/run_unreal_python_via_mcp.py \
      --file Scripts/OpenMassCrowd/verify_central_crowd_gate_runtime.py \
      --script-arg=--gate --script-arg=300

Every run writes timestamped and stable JSON evidence under
``Saved/Reports/OpenMassCrowd``.  A host-only smoke test is available with::

    python Scripts/OpenMassCrowd/verify_central_crowd_gate_runtime.py --self-test
"""

from __future__ import annotations

import builtins
import json
import math
import os
import re
import stat
import sys
import time
import traceback
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

try:
    import unreal
except ImportError:  # Allows the pure helper self-test to run on the host.
    unreal = None


ALLOWED_GATES = (30, 100, 200, 300)
SAMPLE_SECONDS_BY_GATE = {30: 60.0, 100: 60.0, 200: 60.0, 300: 60.0}
POLL_INTERVAL_SECONDS = 0.25
EVIDENCE_INTERVAL_SECONDS = 1.0
WAIT_TIMEOUT_SECONDS = 1800.0
GATE_MISMATCH_GRACE_SECONDS = 10.0

MINIMUM_MOVING_RATIO_300 = 0.95
MAXIMUM_STUCK_RATIO_300 = 0.02
MAXIMUM_P95_FRAME_MS_300 = 33.0
MINIMUM_FRAME_WINDOW_SECONDS_300 = 55.0
MOVEMENT_TELEMETRY_WARMUP_SECONDS = 5.0
HIGH_ACTOR_BUDGET = 24
LOW_ACTOR_BUDGET = 72
EXPECTED_CELLS = 6
EXPECTED_DISTRICTS = 6
EXPECTED_DISTRICT_POPULATION = 50
EXPECTED_MAP_PACKAGE = "/Game/Maps/shanghai"
MINIMUM_CERTIFIED_GEOGRAPHIC_BLOCKS = 4
MINIMUM_CONNECTED_STREET_BLOCKS = 4
MINIMUM_JUNCTIONS = 8
MINIMUM_DIRECTIONAL_LANE_LENGTH_METERS = 3000.0
FULL_CENTRAL_POPULATION = 300
# OpenSpec defines a severe center-distance overlap as strictly below 20 cm.
# The implementation still targets 55 cm for initial placement and same-lane
# following, but that quality target is advisory: certified opposite-direction
# right tracks are commonly only about 20 cm apart and cannot truthfully satisfy
# a whole-network 55 cm cross-direction gate.
SEVERE_OVERLAP_THRESHOLD_CM = 20.0
MINIMUM_HARD_SPAWN_CLEARANCE_CM = SEVERE_OVERLAP_THRESHOLD_CM
PLANNED_SPAWN_CLEARANCE_QUALITY_TARGET_CM = 55.0
MAXIMUM_GROUND_GUARD_QUERIES_PER_FRAME = 48
MAXIMUM_CANDIDATE_COMPONENT_TESTS_PER_GUARD = 256.0
EXPECTED_SIGNAL_SOURCES = 30
EXPECTED_SIGNAL_RAY_GEOMETRIES = 1920
EXPECTED_SIGNAL_GEOMETRIES_PER_COLOR = 480
SIGNAL_COLORS = ("Green", "Yellow", "Orange", "Red")
SIGNAL_SOURCE_LABEL = re.compile(r"^SIG_Source_\d{2}_Direct_Roof$")
SIGNAL_RAY_LABEL = re.compile(
    r"^SIG_Ray_\d{3}_(?:Segment|RoofHit)_\d{2}_"
    r"(Green|Yellow|Orange|Red)$"
)

CALLBACK_KEY = "_hk_open_mass_central_gate_verify_handle"
STATE_KEY = "_hk_open_mass_central_gate_verify_state"
REPORT_PREFIX = "central_crowd_gate_runtime"
REPORT_SCHEMA_VERSION = 4


# This is the complete reflected getter contract in OpenMassCrowdSpawner.h.
# Keeping it explicit makes a stale editor module an evidence failure instead
# of silently accepting missing telemetry.
BASE_GETTERS = {
    "network_mode": "get_network_mode",
    "spawned_entities": "get_spawned_entity_count",
    "runtime_network_nodes": "get_runtime_network_node_count",
    "runtime_directed_lanes": "get_runtime_lane_count",
    "route_assignments": "get_route_assignment_count",
    "completed_trips": "get_completed_trip_count",
    "short_path_chunks": "get_central_short_path_chunk_count",
    "route_replans": "get_route_replan_count",
    "ground_projection_failures": "get_ground_projection_failure_count",
    "ground_center_recoveries": "get_ground_center_recovery_count",
    "ground_rollbacks": "get_ground_rollback_count",
    "ground_unrecoverable": "get_ground_unrecoverable_count",
    "current_unsupported_visuals": "get_current_unsupported_visual_count",
    "current_high_res_representations": "get_current_high_res_representation_count",
    "current_low_res_representations": "get_current_low_res_representation_count",
}

CENTRAL_GETTERS = {
    "central_ground_guard_queries": "get_central_ground_guard_query_count",
    "central_ground_candidate_component_tests": (
        "get_central_ground_candidate_component_test_count"
    ),
    "central_ground_component_cache_refreshes": (
        "get_central_ground_component_cache_refresh_count"
    ),
    "central_admission_target": "get_central_admission_target_count",
    "central_admitted": "get_central_admitted_entity_count",
    "central_simulated": "get_central_simulated_entity_count",
    "central_represented": "get_central_represented_entity_count",
    "central_admission_batches": "get_central_admission_batch_count",
    "central_maximum_committed_admission_batch_size": (
        "get_central_maximum_committed_admission_batch_size"
    ),
    "central_planned_spawn_slots": "get_central_planned_spawn_slot_count",
    "central_minimum_planned_spawn_clearance_cm": (
        "get_central_minimum_planned_spawn_clearance_cm"
    ),
    "central_expected_moving": "get_central_expected_moving_entity_count",
    "central_moving": "get_central_moving_entity_count",
    "central_stuck": "get_central_stuck_entity_count",
    "central_severe_overlap_pairs": "get_central_severe_overlap_pair_count",
    "central_severe_overlap_agents": "get_central_severe_overlap_agent_count",
    "central_peak_severe_overlap_pairs": (
        "get_central_peak_severe_overlap_pair_count"
    ),
    "central_peak_severe_overlap_agents": (
        "get_central_peak_severe_overlap_agent_count"
    ),
    "central_severe_overlap_pair_observations": (
        "get_central_severe_overlap_pair_observation_count"
    ),
    "central_minimum_entity_center_distance_cm": (
        "get_central_minimum_entity_center_distance_cm"
    ),
    "central_minimum_observed_entity_center_distance_cm": (
        "get_central_minimum_observed_entity_center_distance_cm"
    ),
    "central_invalid_position_observations": (
        "get_central_invalid_position_observation_count"
    ),
    "central_high_actor_representations": (
        "get_central_high_actor_representation_count"
    ),
    "central_low_actor_representations": (
        "get_central_low_actor_representation_count"
    ),
    "central_actor_representations": "get_central_actor_representation_count",
    "central_vat_representations": "get_central_vat_representation_count",
    "central_frame_time_p50_ms": "get_central_frame_time_p50_ms",
    "central_frame_time_p95_ms": "get_central_frame_time_p95_ms",
    "central_frame_time_maximum_ms": "get_central_frame_time_maximum_ms",
    "central_frame_time_samples": "get_central_frame_time_sample_count",
    "central_frame_time_window_seconds": "get_central_frame_time_window_seconds",
    "central_telemetry_observations": "get_central_telemetry_observation_count",
    "central_local_conflict_resources": (
        "get_central_local_conflict_resource_count"
    ),
    "central_local_conflict_clusters": (
        "get_central_local_conflict_cluster_count"
    ),
    "central_strict_local_conflict_pairs": (
        "get_central_strict_local_conflict_pair_count"
    ),
    "central_uncovered_local_conflict_pairs": (
        "get_central_uncovered_local_conflict_pair_count"
    ),
    "central_admission_clearance_scans": (
        "get_central_admission_clearance_scan_count"
    ),
    "central_admission_clearance_violations": (
        "get_central_admission_clearance_violation_count"
    ),
    "central_maximum_conflict_wait_seconds": (
        "get_central_maximum_conflict_wait_seconds"
    ),
    "central_conflict_wait_replans": "get_central_conflict_wait_replan_count",
}


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def parse_gate(value):
    """Return an allowed gate integer from an integer or enum-like string."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        candidate = int(value)
        return candidate if candidate in ALLOWED_GATES else None
    raw_text = str(value).strip().upper()
    # UE 5.7 reflects a zero-valued enum as, for example,
    # ``<OpenMassCrowdCentralPopulationGate.GATE30: 0>``.  That representation
    # does not end in GATE30, so recognize the explicit enum token anywhere in
    # the string before applying the compact legacy parsing below.
    enum_match = re.search(r"(?:^|[^A-Z0-9])GATE(30|100|200|300)(?!\d)", raw_text)
    if enum_match:
        candidate = int(enum_match.group(1))
        return candidate if candidate in ALLOWED_GATES else None
    normalized = raw_text.replace("_", "").replace("-", "")
    for gate in reversed(ALLOWED_GATES):
        if normalized == str(gate) or normalized.endswith("GATE{}".format(gate)):
            return gate
    return None


def gate_argument_raw(argv):
    """Read an optional --gate value without consuming unrelated Editor args."""
    for index, argument in enumerate(argv):
        text = str(argument)
        if text.startswith("--gate="):
            return text.split("=", 1)[1]
        if text == "--gate":
            return str(argv[index + 1]) if index + 1 < len(argv) else ""
    return None


def json_safe(value):
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        # JSON's NaN/Infinity spellings are not valid machine-readable JSON.
        # Treat non-finite reflected telemetry as missing so its gate check
        # fails closed while the evidence file itself remains standards-valid.
        return value if math.isfinite(value) else None
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    try:
        return value.get_path_name()
    except Exception:
        return str(value)


def object_path(value):
    if value is None:
        return None
    try:
        return value.get_path_name()
    except Exception:
        return str(value)


def canonical_world_package(value):
    """Normalize a PIE world object/package path to its source map package."""
    if value is None:
        return None
    package = str(value).split(":", 1)[0].split(".", 1)[0]
    return re.sub(r"(^|/)UEDPIE_\d+_", r"\1", package)


def append_error(state, stage, error):
    errors = state.setdefault("errors", [])
    text = repr(error)
    if any(item["stage"] == stage and item["error"] == text for item in errors):
        return
    if len(errors) >= 200:
        return
    trace = traceback.format_exc()
    if trace.strip() == "NoneType: None":
        trace = ""
    errors.append(
        {
            "utc": utc_now(),
            "stage": stage,
            "error": text,
            "traceback": trace.replace("\n", " | "),
        }
    )


def get_pie_world():
    subsystem = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem)
    world = subsystem.get_game_world()
    if world is not None:
        return world
    fallback = getattr(unreal.EditorLevelLibrary, "get_game_world", None)
    return fallback() if fallback is not None else None


def actors_of_class(world, actor_class):
    if world is None or actor_class is None:
        return []
    return list(unreal.GameplayStatics.get_all_actors_of_class(world, actor_class))


def actor_label(actor):
    try:
        return str(actor.get_actor_label())
    except Exception:
        return str(actor.get_name())


def visible_static_mesh_geometry_for_actor(actor):
    """Return visible geometry, assigned meshes, and visible mesh components."""

    static_mesh_class = getattr(unreal, "StaticMeshComponent", None)
    ism_class = getattr(unreal, "InstancedStaticMeshComponent", None)
    if static_mesh_class is None:
        return 0, 0, 0

    geometry_count = 0
    assigned_component_count = 0
    visible_component_count = 0
    for component in actor.get_components_by_class(static_mesh_class):
        getter = getattr(component, "get_static_mesh", None)
        mesh = getter() if getter is not None else None
        if mesh is None:
            try:
                mesh = component.get_editor_property("static_mesh")
            except Exception:
                mesh = None
        if mesh is None:
            continue
        assigned_component_count += 1
        try:
            hidden_in_game = bool(component.get_editor_property("hidden_in_game"))
        except Exception:
            hidden_in_game = False
        if not component.is_visible() or hidden_in_game:
            continue
        visible_component_count += 1
        if ism_class is not None and isinstance(component, ism_class):
            instance_getter = getattr(component, "get_instance_count", None)
            geometry_count += (
                int(instance_getter()) if instance_getter is not None else 0
            )
        else:
            geometry_count += 1
    return geometry_count, assigned_component_count, visible_component_count


def collect_signal_scene(world, state):
    """Count the unchanged telecom scene independently of crowd mode."""

    source_labels = []
    ray_labels = []
    ray_geometry_count = 0
    assigned_mesh_components = 0
    visible_mesh_components = 0
    labels_without_visible_geometry = []
    color_geometry_counts = Counter({color: 0 for color in SIGNAL_COLORS})
    for actor in actors_of_class(world, unreal.Actor):
        label = actor_label(actor)
        if SIGNAL_SOURCE_LABEL.fullmatch(label):
            source_labels.append(label)
        match = SIGNAL_RAY_LABEL.fullmatch(label)
        if match is None:
            continue
        ray_labels.append(label)
        try:
            geometry, assigned, visible = visible_static_mesh_geometry_for_actor(actor)
        except Exception as error:
            append_error(state, "signal_ray_visible_geometry", error)
            geometry, assigned, visible = 0, 0, 0
        ray_geometry_count += geometry
        assigned_mesh_components += assigned
        visible_mesh_components += visible
        color_geometry_counts[match.group(1)] += geometry
        if geometry <= 0:
            labels_without_visible_geometry.append(label)

    return {
        "source_actor_count": len(source_labels),
        "unique_source_label_count": len(set(source_labels)),
        "ray_actor_count": len(ray_labels),
        "unique_ray_label_count": len(set(ray_labels)),
        "ray_geometry_count": ray_geometry_count,
        "assigned_mesh_component_count": assigned_mesh_components,
        "visible_mesh_component_count": visible_mesh_components,
        "ray_labels_without_visible_geometry_count": len(
            labels_without_visible_geometry
        ),
        "ray_labels_without_visible_geometry_samples": sorted(
            labels_without_visible_geometry
        )[:40],
        "color_geometry_counts": dict(color_geometry_counts),
    }


def read_property(value, property_name, state, stage, default=None):
    try:
        return value.get_editor_property(property_name)
    except Exception as first_error:
        try:
            return getattr(value, property_name)
        except Exception:
            append_error(state, stage, first_error)
            return default


def call_getter(spawner, getter_name, state):
    try:
        getter = getattr(spawner, getter_name)
    except Exception as error:
        append_error(state, "missing_getter." + getter_name, error)
        return None
    try:
        return json_safe(getter())
    except Exception as error:
        append_error(state, "getter." + getter_name, error)
        return None


def bool_property(value, name, state, stage):
    result = read_property(value, name, state, stage, default=None)
    return None if result is None else bool(result)


def name_property(value, name, state, stage):
    result = read_property(value, name, state, stage, default=None)
    return None if result is None else str(result)


def int_property(value, name, state, stage):
    result = read_property(value, name, state, stage, default=None)
    try:
        return int(result)
    except (TypeError, ValueError):
        if result is not None:
            append_error(state, stage, ValueError("expected integer: {!r}".format(result)))
        return None


def float_property(value, name, state, stage):
    result = read_property(value, name, state, stage, default=None)
    try:
        candidate = float(result)
    except (TypeError, ValueError):
        if result is not None:
            append_error(state, stage, ValueError("expected float: {!r}".format(result)))
        return None
    if not math.isfinite(candidate):
        append_error(state, stage, ValueError("expected finite float: {!r}".format(result)))
        return None
    return candidate


def audit_network_asset(spawner, state):
    asset = read_property(
        spawner,
        "central_network_asset",
        state,
        "spawner.central_network_asset",
        default=None,
    )
    if asset is None:
        return {"asset": None, "available": False}

    result = {"asset": object_path(asset), "available": True}
    for result_key, method_name in (
        ("schema_supported", "is_schema_version_supported"),
        ("complete_compatibility_hashes", "has_complete_compatibility_hashes"),
        ("certified_directional_lane_count", "get_certified_directional_lane_count"),
        ("certified_directional_lane_length_meters", "get_certified_directional_lane_length_meters"),
        ("configured_population", "get_configured_population"),
    ):
        try:
            result[result_key] = json_safe(getattr(asset, method_name)())
        except Exception as error:
            append_error(state, "network_asset." + method_name, error)
            result[result_key] = None

    cells = read_property(asset, "cells", state, "network_asset.cells", default=[])
    components = read_property(
        asset, "components", state, "network_asset.components", default=[]
    )
    districts = read_property(
        asset, "spawn_districts", state, "network_asset.spawn_districts", default=[]
    )
    evidence = read_property(
        asset, "evidence", state, "network_asset.evidence", default=None
    )
    result["cell_count"] = len(cells or [])
    result["district_count"] = len(districts or [])
    result["connected_component_count"] = (
        int_property(
            evidence,
            "connected_component_count",
            state,
            "network_asset.evidence.connected_component_count",
        )
        if evidence is not None
        else None
    )
    result["independent_cycle_count"] = (
        int_property(
            evidence,
            "street_block_count",
            state,
            "network_asset.evidence.street_block_count",
        )
        if evidence is not None
        else None
    )
    result["certified_geographic_block_count"] = (
        int_property(
            evidence,
            "certified_geographic_block_count",
            state,
            "network_asset.evidence.certified_geographic_block_count",
        )
        if evidence is not None
        else None
    )
    result["junction_count"] = (
        int_property(
            evidence,
            "junction_count",
            state,
            "network_asset.evidence.junction_count",
        )
        if evidence is not None
        else None
    )
    result["evidence_certified_directional_lane_length_cm"] = (
        float_property(
            evidence,
            "certified_directional_lane_length_cm",
            state,
            "network_asset.evidence.certified_directional_lane_length_cm",
        )
        if evidence is not None
        else None
    )
    result["evidence_spawn_district_count"] = (
        int_property(
            evidence,
            "spawn_district_count",
            state,
            "network_asset.evidence.spawn_district_count",
        )
        if evidence is not None
        else None
    )
    result["whole_area_recertification_count"] = (
        int_property(
            evidence,
            "whole_area_recertification_count",
            state,
            "network_asset.evidence.whole_area_recertification_count",
        )
        if evidence is not None
        else None
    )

    cell_ids = []
    all_node_ids = set()
    all_lane_ids = set()
    certified_cells = 0
    cells_with_lanes = 0
    total_nodes = 0
    total_lanes = 0
    for index, cell in enumerate(cells or []):
        prefix = "network_asset.cells[{}]".format(index)
        cell_ids.append(name_property(cell, "cell_id", state, prefix + ".cell_id"))
        if bool_property(cell, "certified", state, prefix + ".certified"):
            certified_cells += 1
        lanes = read_property(
            cell, "directed_lanes", state, prefix + ".directed_lanes", default=[]
        )
        nodes = read_property(cell, "nodes", state, prefix + ".nodes", default=[])
        for node_index, node in enumerate(nodes or []):
            node_id = name_property(
                node,
                "node_id",
                state,
                "{}.nodes[{}].node_id".format(prefix, node_index),
            )
            if node_id:
                all_node_ids.add(node_id)
        for lane_index, lane in enumerate(lanes or []):
            lane_id = name_property(
                lane,
                "lane_id",
                state,
                "{}.directed_lanes[{}].lane_id".format(prefix, lane_index),
            )
            if lane_id:
                all_lane_ids.add(lane_id)
        total_nodes += len(nodes or [])
        total_lanes += len(lanes or [])
        if lanes:
            cells_with_lanes += 1
    result["cell_ids"] = sorted(item for item in cell_ids if item)
    result["certified_cell_count"] = certified_cells
    result["cells_with_lanes"] = cells_with_lanes
    result["node_count"] = total_nodes
    result["directed_lane_count_from_cells"] = total_lanes

    component_ids = []
    component_cell_ids = set()
    component_node_ids = []
    component_lane_ids = []
    certified_components = 0
    component_details = []
    component_cell_ids_by_id = {}
    component_node_ids_by_id = {}
    component_lane_ids_by_id = {}
    component_directional_lane_length_cm = 0.0
    component_junction_count = 0
    component_independent_cycle_count = 0
    component_evidence_complete = True
    for index, component in enumerate(components or []):
        prefix = "network_asset.components[{}]".format(index)
        component_id = name_property(
            component, "component_id", state, prefix + ".component_id"
        )
        cells_for_component = [
            str(item)
            for item in (
                read_property(
                    component, "cell_ids", state, prefix + ".cell_ids", default=[]
                )
                or []
            )
        ]
        nodes_for_component = [
            str(item)
            for item in (
                read_property(
                    component, "node_ids", state, prefix + ".node_ids", default=[]
                )
                or []
            )
        ]
        lanes_for_component = [
            str(item)
            for item in (
                read_property(
                    component,
                    "directed_lane_ids",
                    state,
                    prefix + ".directed_lane_ids",
                    default=[],
                )
                or []
            )
        ]
        certified = bool_property(
            component, "certified", state, prefix + ".certified"
        )
        if certified:
            certified_components += 1
        component_ids.append(component_id)
        component_cell_ids.update(cells_for_component)
        component_node_ids.extend(nodes_for_component)
        component_lane_ids.extend(lanes_for_component)
        if component_id:
            component_cell_ids_by_id[component_id] = set(cells_for_component)
            component_node_ids_by_id[component_id] = set(nodes_for_component)
            component_lane_ids_by_id[component_id] = set(lanes_for_component)
        directional_lane_length_cm = float_property(
            component,
            "directional_lane_length_cm",
            state,
            prefix + ".directional_lane_length_cm",
        )
        junction_count = int_property(
            component,
            "junction_count",
            state,
            prefix + ".junction_count",
        )
        independent_cycle_count = int_property(
            component,
            "street_block_count",
            state,
            prefix + ".street_block_count",
        )
        if (
            directional_lane_length_cm is None
            or junction_count is None
            or independent_cycle_count is None
        ):
            component_evidence_complete = False
        else:
            component_directional_lane_length_cm += directional_lane_length_cm
            component_junction_count += junction_count
            component_independent_cycle_count += independent_cycle_count
        component_details.append(
            {
                "component_id": component_id,
                "certified": certified,
                "cell_ids": cells_for_component,
                "node_count": len(nodes_for_component),
                "directed_lane_count": len(lanes_for_component),
                "directional_lane_length_cm": directional_lane_length_cm,
                "junction_count": junction_count,
                "independent_cycle_count": independent_cycle_count,
            }
        )
    unique_component_ids = {item for item in component_ids if item}
    result["component_count"] = len(components or [])
    result["certified_component_count"] = certified_components
    result["component_ids"] = sorted(unique_component_ids)
    result["components"] = component_details
    result["component_directional_lane_length_cm"] = (
        component_directional_lane_length_cm
    )
    result["component_junction_count"] = component_junction_count
    result["component_independent_cycle_count"] = (
        component_independent_cycle_count
    )
    result["component_evidence_complete"] = component_evidence_complete
    result["component_partition_passed"] = bool(
        result["component_count"] > 0
        and certified_components == result["component_count"]
        and len(unique_component_ids) == result["component_count"]
        and component_cell_ids == set(result["cell_ids"])
        and len(component_node_ids) == len(set(component_node_ids))
        and set(component_node_ids) == all_node_ids
        and len(component_lane_ids) == len(set(component_lane_ids))
        and set(component_lane_ids) == all_lane_ids
    )

    district_ids = []
    enabled_seeded_districts = 0
    district_cell_ids = set()
    district_component_ids = set()
    district_component_membership_passed = True
    district_population_balance_passed = True
    district_details = []
    for index, district in enumerate(districts or []):
        prefix = "network_asset.spawn_districts[{}]".format(index)
        district_id = name_property(
            district, "district_id", state, prefix + ".district_id"
        )
        component_id = name_property(
            district, "component_id", state, prefix + ".component_id"
        )
        enabled = bool_property(district, "enabled", state, prefix + ".enabled")
        cells_for_district = read_property(
            district, "cell_ids", state, prefix + ".cell_ids", default=[]
        )
        nodes = read_property(
            district, "spawn_node_ids", state, prefix + ".spawn_node_ids", default=[]
        )
        lanes = read_property(
            district, "spawn_lane_ids", state, prefix + ".spawn_lane_ids", default=[]
        )
        normalized_cells = [str(item) for item in (cells_for_district or [])]
        normalized_nodes = [str(item) for item in (nodes or [])]
        normalized_lanes = [str(item) for item in (lanes or [])]
        target_population = int_property(
            district,
            "target_population",
            state,
            prefix + ".target_population",
        )
        district_cell_ids.update(normalized_cells)
        if component_id:
            district_component_ids.add(component_id)
        if (
            not component_id
            or component_id not in unique_component_ids
            or not set(normalized_cells).issubset(
                component_cell_ids_by_id.get(component_id, set())
            )
            or not set(normalized_nodes).issubset(
                component_node_ids_by_id.get(component_id, set())
            )
            or not set(normalized_lanes).issubset(
                component_lane_ids_by_id.get(component_id, set())
            )
        ):
            district_component_membership_passed = False
        if target_population != EXPECTED_DISTRICT_POPULATION:
            district_population_balance_passed = False
        if enabled and normalized_cells and nodes and lanes:
            enabled_seeded_districts += 1
        district_ids.append(district_id)
        district_details.append(
            {
                "district_id": district_id,
                "component_id": component_id,
                "enabled": enabled,
                "cell_ids": normalized_cells,
                "spawn_node_count": len(nodes or []),
                "spawn_lane_count": len(lanes or []),
                "target_population": target_population,
            }
        )
    result["district_ids"] = sorted(item for item in district_ids if item)
    result["enabled_seeded_district_count"] = enabled_seeded_districts
    result["district_component_count"] = len(district_component_ids)
    result["district_component_ids"] = sorted(district_component_ids)
    result["district_component_membership_passed"] = (
        district_component_membership_passed
    )
    result["district_population_balance_passed"] = (
        district_population_balance_passed
    )
    result["district_cell_ids"] = sorted(district_cell_ids)
    result["districts"] = district_details
    result["connected_six_district_topology_passed"] = bool(
        result["schema_supported"]
        and result["complete_compatibility_hashes"]
        and result["cell_count"] == EXPECTED_CELLS
        and result["certified_cell_count"] == EXPECTED_CELLS
        and result["cells_with_lanes"] == EXPECTED_CELLS
        and result["district_count"] == EXPECTED_DISTRICTS
        and result["enabled_seeded_district_count"] == EXPECTED_DISTRICTS
        and len(set(result["cell_ids"])) == EXPECTED_CELLS
        and set(result["district_cell_ids"]) == set(result["cell_ids"])
        and result["connected_component_count"] == 1
        and result["component_count"] == 1
        and result["component_partition_passed"]
        and result["district_component_count"] == 1
        and result["district_component_membership_passed"]
        and result["district_population_balance_passed"]
        and result["evidence_spawn_district_count"] == EXPECTED_DISTRICTS
        and result["whole_area_recertification_count"] == 0
        and (result["certified_directional_lane_count"] or 0) > 0
        and result["directed_lane_count_from_cells"]
        == result["certified_directional_lane_count"]
        and result["node_count"] > 0
        and (result["configured_population"] or 0) == 300
    )
    reported_length_m = result.get("certified_directional_lane_length_meters")
    evidence_length_cm = result.get(
        "evidence_certified_directional_lane_length_cm"
    )
    length_evidence_consistent = bool(
        isinstance(reported_length_m, (int, float))
        and math.isfinite(float(reported_length_m))
        and isinstance(evidence_length_cm, (int, float))
        and math.isfinite(float(evidence_length_cm))
        and math.isclose(
            float(reported_length_m) * 100.0,
            float(evidence_length_cm),
            rel_tol=0.01,
            abs_tol=2.0,
        )
    )
    component_evidence_consistent = bool(
        result.get("component_evidence_complete")
        and result.get("junction_count") == result.get("component_junction_count")
        and result.get("independent_cycle_count")
        == result.get("component_independent_cycle_count")
        and isinstance(evidence_length_cm, (int, float))
        and math.isclose(
            float(evidence_length_cm),
            float(result.get("component_directional_lane_length_cm", 0.0)),
            rel_tol=0.01,
            abs_tol=2.0,
        )
    )
    result["component_evidence_consistent"] = component_evidence_consistent
    result["coverage_minimums_passed"] = bool(
        (result.get("certified_geographic_block_count") or 0)
        >= MINIMUM_CERTIFIED_GEOGRAPHIC_BLOCKS
        and result.get("certified_geographic_block_count")
        == result.get("cells_with_lanes")
        and (result.get("independent_cycle_count") or 0)
        >= MINIMUM_CONNECTED_STREET_BLOCKS
        and (result.get("junction_count") or 0) >= MINIMUM_JUNCTIONS
        and (reported_length_m or 0.0)
        >= MINIMUM_DIRECTIONAL_LANE_LENGTH_METERS
        and length_evidence_consistent
        and component_evidence_consistent
    )
    result["runtime_asset_acceptance_passed"] = bool(
        result["connected_six_district_topology_passed"]
        and result["coverage_minimums_passed"]
    )
    return result


def collect_snapshot(world, state):
    spawner_class = getattr(unreal, "OpenMassCrowdSpawner", None)
    if spawner_class is None:
        append_error(
            state,
            "reflection.OpenMassCrowdSpawner",
            RuntimeError("OpenMassCrowdSpawner is not reflected; rebuild/restart editor"),
        )
        spawners = []
    else:
        spawners = actors_of_class(world, spawner_class)
    spawners.sort(key=lambda actor: actor.get_path_name())

    snapshot = {
        "utc": utc_now(),
        "world": object_path(world),
        "world_name": world.get_name(),
        "canonical_world_package": canonical_world_package(object_path(world)),
        "spawner_count": len(spawners),
        "spawners": [object_path(actor) for actor in spawners],
        "population_property": None,
        "population_gate_property": None,
        "central_admission_batch_size_property": None,
        "central_admission_batch_interval_property": None,
        "metrics": {},
    }
    if len(spawners) != 1:
        return snapshot

    spawner = spawners[0]
    snapshot["population_property"] = int_property(
        spawner, "population_count", state, "spawner.population_count"
    )
    gate_value = read_property(
        spawner,
        "central_population_gate",
        state,
        "spawner.central_population_gate",
        default=None,
    )
    snapshot["population_gate_property"] = json_safe(gate_value)
    snapshot["population_gate_inferred"] = parse_gate(gate_value)
    snapshot["central_admission_batch_size_property"] = int_property(
        spawner,
        "central_admission_batch_size",
        state,
        "spawner.central_admission_batch_size",
    )
    snapshot["central_admission_batch_interval_property"] = float_property(
        spawner,
        "central_admission_batch_interval",
        state,
        "spawner.central_admission_batch_interval",
    )

    for metric_name, getter_name in {**BASE_GETTERS, **CENTRAL_GETTERS}.items():
        snapshot["metrics"][metric_name] = call_getter(
            spawner, getter_name, state
        )

    if (
        state.get("network_asset_audit") is None
        or not state["network_asset_audit"].get("available")
    ):
        state["network_asset_audit"] = audit_network_asset(spawner, state)
    snapshot["network_asset"] = state.get("network_asset_audit")
    return snapshot


def is_central_network_mode(value):
    if value is None:
        return False
    text = str(value).upper()
    if "CENTRAL_CERTIFIED_CACHE" in text or "CENTRALCERTIFIEDCACHE" in text.replace("_", ""):
        return True
    try:
        return int(value) == 1
    except (TypeError, ValueError):
        return False


def infer_target(snapshot):
    metrics = snapshot.get("metrics", {})
    for value in (
        metrics.get("central_admission_target"),
        snapshot.get("population_gate_inferred"),
        snapshot.get("population_property"),
    ):
        gate = parse_gate(value)
        if gate is not None:
            return gate
    return None


def snapshot_ready(snapshot, target):
    metrics = snapshot.get("metrics", {})
    asset = snapshot.get("network_asset") or {}
    return bool(
        target in ALLOWED_GATES
        and snapshot.get("canonical_world_package") == EXPECTED_MAP_PACKAGE
        and snapshot.get("spawner_count") == 1
        and is_central_network_mode(metrics.get("network_mode"))
        and metrics.get("central_admission_target") == target
        and metrics.get("central_admitted") == target
        and metrics.get("central_simulated") == target
        and metrics.get("central_represented") == target
        and metrics.get("spawned_entities") == target
        and (metrics.get("runtime_network_nodes") or 0) > 0
        and (metrics.get("runtime_directed_lanes") or 0) > 0
        and (metrics.get("route_assignments") or 0) >= target
        and asset.get("runtime_asset_acceptance_passed") is True
    )


def core_population_exact(snapshot, target):
    metrics = snapshot.get("metrics", {})
    return all(
        metrics.get(name) == target
        for name in (
            "central_admission_target",
            "central_admitted",
            "central_simulated",
            "central_represented",
            "spawned_entities",
        )
    ) and snapshot.get("population_property") == target and (
        snapshot.get("population_gate_inferred") == target
    )


def sample_summary(snapshot, elapsed_game_seconds, elapsed_wall_seconds):
    return {
        "utc": utc_now(),
        "elapsed_game_seconds": round(elapsed_game_seconds, 3),
        "elapsed_wall_seconds": round(elapsed_wall_seconds, 3),
        "metrics": dict(snapshot.get("metrics", {})),
    }


def add_status(state, status, snapshot=None):
    update = {
        "utc": utc_now(),
        "wall_seconds": round(time.monotonic() - state["started_monotonic"], 3),
        "status": status,
        "requested_gate": state.get("target"),
    }
    if snapshot is not None:
        metrics = snapshot.get("metrics", {})
        update.update(
            {
                "spawner_count": snapshot.get("spawner_count"),
                "inferred_gate": infer_target(snapshot),
                "admitted": metrics.get("central_admitted"),
                "simulated": metrics.get("central_simulated"),
                "represented": metrics.get("central_represented"),
                "moving": metrics.get("central_moving"),
                "stuck": metrics.get("central_stuck"),
                "unsupported": metrics.get("current_unsupported_visuals"),
                "overlap_pairs": metrics.get("central_severe_overlap_pairs"),
                "peak_overlap_pairs": metrics.get(
                    "central_peak_severe_overlap_pairs"
                ),
            }
        )
    comparable = {key: value for key, value in update.items() if key not in {"utc", "wall_seconds"}}
    previous = state.get("status_updates", [])[-1] if state.get("status_updates") else None
    previous_comparable = (
        {key: value for key, value in previous.items() if key not in {"utc", "wall_seconds"}}
        if previous
        else None
    )
    if comparable == previous_comparable:
        return
    state.setdefault("status_updates", []).append(update)
    unreal.log_warning(
        "OPEN_MASS_CENTRAL_GATE_VERIFY_STATUS="
        + json.dumps(update, ensure_ascii=False, sort_keys=True)
    )


def add_check(checks, name, passed, actual, expected, required=True):
    checks[name] = {
        "required": bool(required),
        "passed": bool(passed),
        "actual": json_safe(actual),
        "expected": json_safe(expected),
    }


def missing_getter_names(state):
    missing = []
    for item in state.get("errors", []):
        if item["stage"].startswith("missing_getter."):
            missing.append(item["stage"].split(".", 1)[1])
    return sorted(set(missing))


def observed_max(state, metric_name):
    values = [
        sample["metrics"].get(metric_name)
        for sample in state.get("samples", [])
        if sample["metrics"].get(metric_name) is not None
    ]
    return max(values) if values else None


def overlap_window_passes(
    start_observations,
    final_observations,
    maximum_current_pairs,
    maximum_current_agents,
):
    """Require no new severe-overlap evidence in the sampled steady window."""
    values = (
        start_observations,
        final_observations,
        maximum_current_pairs,
        maximum_current_agents,
    )
    if any(value is None or isinstance(value, bool) for value in values):
        return False
    return bool(
        final_observations >= start_observations
        and final_observations - start_observations == 0
        and maximum_current_pairs == 0
        and maximum_current_agents == 0
    )


def center_clearance_passes(current_distance_cm, minimum_observed_distance_cm):
    """Require the sampled runtime to preserve the OpenSpec 20 cm hard floor."""
    values = (current_distance_cm, minimum_observed_distance_cm)
    if any(
        value is None
        or isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        for value in values
    ):
        return False
    return all(value >= SEVERE_OVERLAP_THRESHOLD_CM for value in values)


def bounded_batch_admission_passes(
    target,
    configured_maximum_batch_size,
    observed_maximum_committed_batch_size,
    batch_interval_seconds,
    batch_count,
):
    """Accept fixed or adaptive-prefix batches while preserving hard bounds."""
    if (
        target not in ALLOWED_GATES
        or not isinstance(configured_maximum_batch_size, int)
        or not 1 <= configured_maximum_batch_size <= 50
        or not isinstance(observed_maximum_committed_batch_size, int)
        or not 1
        <= observed_maximum_committed_batch_size
        <= configured_maximum_batch_size
        or not isinstance(batch_interval_seconds, (int, float))
        or not 0.01 <= batch_interval_seconds <= 2.0
        or not isinstance(batch_count, int)
    ):
        return False
    return (
        math.ceil(target / configured_maximum_batch_size)
        <= batch_count
        <= target
        and batch_count
        >= math.ceil(target / observed_maximum_committed_batch_size)
    )


def post_warmup_values(state, metric_name):
    """Return finite numeric health samples after telemetry has a baseline."""
    values = []
    for sample in state.get("samples", []):
        if (
            sample.get("elapsed_game_seconds", 0.0)
            < MOVEMENT_TELEMETRY_WARMUP_SECONDS
        ):
            continue
        value = sample.get("metrics", {}).get(metric_name)
        if isinstance(value, (int, float)) and math.isfinite(float(value)):
            values.append(value)
    return values


def build_checks(state, reason, final_snapshot):
    checks = {}
    target = state.get("target")
    required_seconds = SAMPLE_SECONDS_BY_GATE.get(target)
    metrics = (final_snapshot or {}).get("metrics", {})
    initial_metrics = (state.get("initial_snapshot") or {}).get("metrics", {})
    network = state.get("network_asset_audit") or {}
    samples = state.get("samples", [])
    game_seconds = state.get("sample_elapsed_game_seconds", 0.0)
    wall_seconds = state.get("sample_elapsed_wall_seconds", 0.0)

    add_check(checks, "completed_sampling", reason == "completed", reason, "completed")
    add_check(
        checks,
        "requested_gate_is_supported",
        target in ALLOWED_GATES,
        target,
        list(ALLOWED_GATES),
    )
    add_check(
        checks,
        "sample_duration_reached",
        required_seconds is not None
        and game_seconds >= required_seconds
        and wall_seconds >= required_seconds,
        {"game_seconds": game_seconds, "wall_seconds": wall_seconds},
        {"minimum_each_seconds": required_seconds},
    )
    add_check(
        checks,
        "exactly_one_runtime_spawner",
        (final_snapshot or {}).get("spawner_count") == 1,
        (final_snapshot or {}).get("spawner_count"),
        1,
    )
    add_check(
        checks,
        "runtime_world_is_shanghai",
        (final_snapshot or {}).get("canonical_world_package")
        == EXPECTED_MAP_PACKAGE,
        {
            "world": (final_snapshot or {}).get("world"),
            "canonical_world_package": (final_snapshot or {}).get(
                "canonical_world_package"
            ),
        },
        {"canonical_world_package": EXPECTED_MAP_PACKAGE},
    )
    add_check(
        checks,
        "central_certified_cache_mode",
        is_central_network_mode(metrics.get("network_mode")),
        metrics.get("network_mode"),
        "CentralCertifiedCache",
    )
    population_actual = {
        name: metrics.get(name)
        for name in (
            "central_admission_target",
            "central_admitted",
            "central_simulated",
            "central_represented",
            "spawned_entities",
        )
    }
    add_check(
        checks,
        "exact_gate_population",
        target in ALLOWED_GATES
        and core_population_exact(final_snapshot or {}, target)
        and all(
            all(sample["metrics"].get(name) == target for name in population_actual)
            for sample in samples
        ),
        {
            **population_actual,
            "population_property": (final_snapshot or {}).get(
                "population_property"
            ),
            "population_gate_property": (final_snapshot or {}).get(
                "population_gate_property"
            ),
            "population_gate_inferred": (final_snapshot or {}).get(
                "population_gate_inferred"
            ),
        },
        {
            **{name: target for name in population_actual},
            "population_property": target,
            "population_gate_inferred": target,
        },
    )
    add_check(
        checks,
        "complete_collision_cleared_spawn_plan",
        metrics.get("central_planned_spawn_slots") == FULL_CENTRAL_POPULATION
        and (
            metrics.get("central_minimum_planned_spawn_clearance_cm") or 0.0
        )
        >= MINIMUM_HARD_SPAWN_CLEARANCE_CM,
        {
            "full_slot_count": metrics.get("central_planned_spawn_slots"),
            "minimum_clearance_cm": metrics.get(
                "central_minimum_planned_spawn_clearance_cm"
            ),
            "active_gate": target,
        },
        {
            "full_slot_count": FULL_CENTRAL_POPULATION,
            "minimum_hard_clearance_cm": MINIMUM_HARD_SPAWN_CLEARANCE_CM,
            "advisory_quality_target_cm": (
                PLANNED_SPAWN_CLEARANCE_QUALITY_TARGET_CM
            ),
            "advisory_not_spec_gate": True,
            "stable_gate_prefixes": list(ALLOWED_GATES),
        },
    )
    add_check(
        checks,
        "certified_connected_six_district_network",
        network.get("runtime_asset_acceptance_passed") is True,
        network,
        {
            "cells": EXPECTED_CELLS,
            "districts": EXPECTED_DISTRICTS,
            "component_count": 1,
            "component_partition": "all nodes/lanes exactly once",
            "district_components": 1,
            "district_membership": (
                "cells/nodes/lanes stay inside each district component"
            ),
            "district_target_population_each": EXPECTED_DISTRICT_POPULATION,
            "connected_street_blocks_minimum": MINIMUM_CONNECTED_STREET_BLOCKS,
            "certified_geographic_blocks_minimum": (
                MINIMUM_CERTIFIED_GEOGRAPHIC_BLOCKS
            ),
            "certified_geographic_blocks": "exact cells_with_lanes",
            "junctions_minimum": MINIMUM_JUNCTIONS,
            "root_component_evidence_consistent": True,
            "certified_directional_lane_length_meters_minimum": (
                MINIMUM_DIRECTIONAL_LANE_LENGTH_METERS
            ),
            "configured_population": 300,
            "all_cells_certified_and_nonempty": True,
            "all_districts_enabled_and_seeded": True,
            "runtime_recertification": 0,
        },
    )
    batch_size = (final_snapshot or {}).get(
        "central_admission_batch_size_property"
    )
    batch_interval = (final_snapshot or {}).get(
        "central_admission_batch_interval_property"
    )
    batch_count = metrics.get("central_admission_batches")
    maximum_committed_batch_size = metrics.get(
        "central_maximum_committed_admission_batch_size"
    )
    minimum_batch_count = (
        math.ceil(target / batch_size)
        if target in ALLOWED_GATES
        and isinstance(batch_size, int)
        and 1 <= batch_size <= 50
        else None
    )
    minimum_count_for_observed_maximum = (
        math.ceil(target / maximum_committed_batch_size)
        if target in ALLOWED_GATES
        and isinstance(maximum_committed_batch_size, int)
        and maximum_committed_batch_size > 0
        else None
    )
    add_check(
        checks,
        "bounded_batched_admission",
        bounded_batch_admission_passes(
            target,
            batch_size,
            maximum_committed_batch_size,
            batch_interval,
            batch_count,
        ),
        {
            "configured_maximum_batch_size": batch_size,
            "observed_maximum_committed_batch_size": (
                maximum_committed_batch_size
            ),
            "batch_interval_seconds": batch_interval,
            "batch_count": batch_count,
            "population": target,
        },
        {
            "configured_batch_size_range": [1, 50],
            "observed_maximum_committed_batch_size_range": [1, batch_size],
            "batch_interval_seconds_range": [0.01, 2.0],
            "batch_count_range": [minimum_batch_count, target],
            "minimum_count_for_observed_maximum": (
                minimum_count_for_observed_maximum
            ),
            "adaptive_prefix_batches_are_complete_transactions": True,
        },
    )
    add_check(
        checks,
        "complete_certified_geometry_conflict_coverage",
        (metrics.get("central_strict_local_conflict_pairs") or 0) > 0
        and metrics.get("central_uncovered_local_conflict_pairs") == 0
        and (metrics.get("central_local_conflict_resources") or 0) > 0
        and (metrics.get("central_local_conflict_clusters") or 0) > 0,
        {
            "strict_local_conflict_pairs": metrics.get(
                "central_strict_local_conflict_pairs"
            ),
            "uncovered_local_conflict_pairs": metrics.get(
                "central_uncovered_local_conflict_pairs"
            ),
            "local_conflict_resources": metrics.get(
                "central_local_conflict_resources"
            ),
            "local_conflict_clusters": metrics.get(
                "central_local_conflict_clusters"
            ),
        },
        {
            "strict_local_conflict_pairs": ">0 for the active certified cache",
            "uncovered_local_conflict_pairs": 0,
            "local_conflict_resources": ">0 for the active certified cache",
            "local_conflict_clusters": ">0 for the active certified cache",
            "threshold_cm": SEVERE_OVERLAP_THRESHOLD_CM,
        },
    )
    add_check(
        checks,
        "every_bounded_admission_batch_was_clearance_scanned",
        batch_count is not None
        and metrics.get("central_admission_clearance_scans") == batch_count
        and metrics.get("central_admission_clearance_violations") == 0,
        {
            "batch_count": batch_count,
            "clearance_scans": metrics.get(
                "central_admission_clearance_scans"
            ),
            "clearance_violations": metrics.get(
                "central_admission_clearance_violations"
            ),
        },
        {
            "clearance_scans": "exactly one immediate scan per admitted batch",
            "clearance_violations": 0,
        },
    )
    maximum_conflict_wait_seconds = metrics.get(
        "central_maximum_conflict_wait_seconds"
    )
    add_check(
        checks,
        "collision_resource_waits_are_bounded_and_replanned",
        isinstance(maximum_conflict_wait_seconds, (int, float))
        and not isinstance(maximum_conflict_wait_seconds, bool)
        and math.isfinite(maximum_conflict_wait_seconds)
        and 0.0 <= maximum_conflict_wait_seconds,
        {
            "maximum_wait_seconds": maximum_conflict_wait_seconds,
            "wait_replans": metrics.get("central_conflict_wait_replans"),
        },
        {
            "maximum_wait_seconds": "finite and non-negative",
            "replan_threshold_seconds": 1.5,
            "advisory_two_second_target": 2.0,
            "advisory_not_spec_gate": True,
        },
    )
    add_check(
        checks,
        "runtime_graph_and_routes_present",
        (metrics.get("runtime_network_nodes") or 0) > 0
        and (metrics.get("runtime_directed_lanes") or 0) > 0
        and metrics.get("runtime_network_nodes") == network.get("node_count")
        and metrics.get("runtime_directed_lanes")
        == network.get("certified_directional_lane_count")
        and (metrics.get("route_assignments") or 0) >= (target or sys.maxsize),
        {
            "nodes": metrics.get("runtime_network_nodes"),
            "directed_lanes": metrics.get("runtime_directed_lanes"),
            "asset_nodes": network.get("node_count"),
            "asset_certified_directional_lanes": network.get(
                "certified_directional_lane_count"
            ),
            "route_assignments": metrics.get("route_assignments"),
            "completed_trips": metrics.get("completed_trips"),
            "route_replans": metrics.get("route_replans"),
        },
        {
            "nodes": "exact asset node count",
            "directed_lanes": "exact asset certified lane count",
            "route_assignments_minimum": target,
        },
    )

    failures = metrics.get("ground_projection_failures")
    recoveries = metrics.get("ground_center_recoveries")
    rollbacks = metrics.get("ground_rollbacks")
    unrecoverable = metrics.get("ground_unrecoverable")
    unsupported_peak = observed_max(state, "current_unsupported_visuals")
    add_check(
        checks,
        "all_ground_failures_accounted_and_no_unsupported_visual",
        failures is not None
        and recoveries is not None
        and rollbacks is not None
        and unrecoverable == 0
        and failures == recoveries + rollbacks + unrecoverable
        and metrics.get("current_unsupported_visuals") == 0
        and unsupported_peak == 0,
        {
            "projection_failures": failures,
            "center_recoveries": recoveries,
            "rollbacks": rollbacks,
            "unrecoverable": unrecoverable,
            "current_unsupported": metrics.get("current_unsupported_visuals"),
            "maximum_sampled_unsupported": unsupported_peak,
            "guard_queries": metrics.get("central_ground_guard_queries"),
            "candidate_component_tests": metrics.get(
                "central_ground_candidate_component_tests"
            ),
            "component_cache_refreshes": metrics.get(
                "central_ground_component_cache_refreshes"
            ),
        },
        {"every_failure_accounted": True, "unrecoverable": 0, "unsupported": 0},
    )
    guard_queries = metrics.get("central_ground_guard_queries")
    candidate_tests = metrics.get("central_ground_candidate_component_tests")
    cache_refreshes = metrics.get("central_ground_component_cache_refreshes")
    initial_guard_queries = initial_metrics.get("central_ground_guard_queries")
    initial_candidate_tests = initial_metrics.get(
        "central_ground_candidate_component_tests"
    )
    initial_cache_refreshes = initial_metrics.get(
        "central_ground_component_cache_refreshes"
    )

    def counter_delta(final_value, initial_value):
        if isinstance(final_value, int) and isinstance(initial_value, int):
            return final_value - initial_value
        return None

    guard_query_delta = counter_delta(guard_queries, initial_guard_queries)
    candidate_test_delta = counter_delta(candidate_tests, initial_candidate_tests)
    cache_refresh_delta = counter_delta(cache_refreshes, initial_cache_refreshes)
    frame_samples = metrics.get("central_frame_time_samples")
    maximum_guard_query_delta = (
        frame_samples * MAXIMUM_GROUND_GUARD_QUERIES_PER_FRAME
        if isinstance(frame_samples, int) and frame_samples > 0
        else None
    )
    candidate_tests_per_guard = (
        candidate_test_delta / guard_query_delta
        if isinstance(candidate_test_delta, int)
        and isinstance(guard_query_delta, int)
        and guard_query_delta > 0
        else None
    )
    minimum_cache_refresh_delta = (
        max(1, int(required_seconds // 2))
        if isinstance(required_seconds, (int, float))
        else None
    )
    add_check(
        checks,
        "bounded_runtime_cesium_ground_guards_exercised_during_sample",
        target in ALLOWED_GATES
        and isinstance(guard_query_delta, int)
        and guard_query_delta >= target
        and maximum_guard_query_delta is not None
        and guard_query_delta <= maximum_guard_query_delta
        and isinstance(candidate_test_delta, int)
        and candidate_test_delta >= guard_query_delta
        and candidate_tests_per_guard is not None
        and candidate_tests_per_guard
        <= MAXIMUM_CANDIDATE_COMPONENT_TESTS_PER_GUARD
        and isinstance(cache_refresh_delta, int)
        and minimum_cache_refresh_delta is not None
        and cache_refresh_delta >= minimum_cache_refresh_delta,
        {
            "sample_start_guard_queries": initial_guard_queries,
            "sample_final_guard_queries": guard_queries,
            "sample_guard_query_delta": guard_query_delta,
            "sample_maximum_guard_query_delta": maximum_guard_query_delta,
            "sample_start_candidate_component_tests": initial_candidate_tests,
            "sample_final_candidate_component_tests": candidate_tests,
            "sample_candidate_component_test_delta": candidate_test_delta,
            "sample_candidate_tests_per_guard": candidate_tests_per_guard,
            "sample_start_component_cache_refreshes": initial_cache_refreshes,
            "sample_final_component_cache_refreshes": cache_refreshes,
            "sample_component_cache_refresh_delta": cache_refresh_delta,
            "frame_samples": frame_samples,
            "population": target,
        },
        {
            "sample_guard_query_delta_minimum": target,
            "guard_queries_per_frame_maximum": (
                MAXIMUM_GROUND_GUARD_QUERIES_PER_FRAME
            ),
            "sample_candidate_component_tests_minimum": "guard query delta",
            "candidate_component_tests_per_guard_maximum": (
                MAXIMUM_CANDIDATE_COMPONENT_TESTS_PER_GUARD
            ),
            "sample_component_cache_refresh_delta_minimum": (
                minimum_cache_refresh_delta
            ),
            "whole_area_recertification": 0,
        },
    )

    current_pairs_peak = observed_max(state, "central_severe_overlap_pairs")
    current_agents_peak = observed_max(state, "central_severe_overlap_agents")
    sample_final_pair_observations = metrics.get(
        "central_severe_overlap_pair_observations"
    )
    lifetime_peak_pairs = metrics.get("central_peak_severe_overlap_pairs")
    lifetime_peak_agents = metrics.get("central_peak_severe_overlap_agents")
    collision_observations = metrics.get("central_telemetry_observations")
    frame_time_samples = metrics.get("central_frame_time_samples")
    current_minimum_center_distance_cm = metrics.get(
        "central_minimum_entity_center_distance_cm"
    )
    minimum_observed_center_distance_cm = metrics.get(
        "central_minimum_observed_entity_center_distance_cm"
    )
    add_check(
        checks,
        "no_severe_overlap_below_20cm_during_steady_60s_window",
        metrics.get("central_severe_overlap_pairs") == 0
        and metrics.get("central_severe_overlap_agents") == 0
        and overlap_window_passes(
            initial_metrics.get("central_severe_overlap_pair_observations"),
            sample_final_pair_observations,
            current_pairs_peak,
            current_agents_peak,
        )
        and metrics.get("central_invalid_position_observations") == 0
        and isinstance(collision_observations, int)
        and not isinstance(collision_observations, bool)
        and isinstance(frame_time_samples, int)
        and not isinstance(frame_time_samples, bool)
        and collision_observations >= frame_time_samples
        and frame_time_samples > 0
        and center_clearance_passes(
            current_minimum_center_distance_cm,
            minimum_observed_center_distance_cm,
        ),
        {
            "current_pairs": metrics.get("central_severe_overlap_pairs"),
            "current_agents": metrics.get("central_severe_overlap_agents"),
            "lifetime_peak_pairs": lifetime_peak_pairs,
            "lifetime_peak_agents": lifetime_peak_agents,
            "maximum_sampled_current_pairs": current_pairs_peak,
            "maximum_sampled_current_agents": current_agents_peak,
            "sample_final_pair_observations": sample_final_pair_observations,
            "invalid_position_observations": metrics.get(
                "central_invalid_position_observations"
            ),
            "complete_session_collision_observations": collision_observations,
            "retained_frame_time_samples": frame_time_samples,
            "current_minimum_center_distance_cm": current_minimum_center_distance_cm,
            "minimum_observed_center_distance_cm": (
                minimum_observed_center_distance_cm
            ),
        },
        {
            "steady_window_current_pairs_and_agents": 0,
            "steady_window_new_pair_observations": 0,
            "complete_session_invalid_position_observations": 0,
            "collision_observations": (
                "at least one complete pair scan per retained game frame"
            ),
            "severe_overlap_threshold_cm": SEVERE_OVERLAP_THRESHOLD_CM,
            "planned_spawn_quality_target_cm": (
                PLANNED_SPAWN_CLEARANCE_QUALITY_TARGET_CM
            ),
            "planned_spawn_quality_is_advisory": True,
        },
    )

    represented = metrics.get("central_represented")
    high = metrics.get("central_high_actor_representations")
    low = metrics.get("central_low_actor_representations")
    actors = metrics.get("central_actor_representations")
    vat = metrics.get("central_vat_representations")
    lod_passed = bool(
        represented == target
        and high is not None
        and low is not None
        and actors is not None
        and vat is not None
        and 0 <= high <= HIGH_ACTOR_BUDGET
        and 0 <= low <= LOW_ACTOR_BUDGET
        and actors == high + low
        and vat == represented - actors
        and high + low + vat == represented
    )
    add_check(
        checks,
        "lod_representations_account_for_population_within_budgets",
        lod_passed,
        {
            "represented": represented,
            "high_actor": high,
            "low_actor": low,
            "actor_total": actors,
            "vat_remainder": vat,
            "legacy_high_res": metrics.get("current_high_res_representations"),
            "legacy_low_res": metrics.get("current_low_res_representations"),
        },
        {
            "high_actor_maximum": HIGH_ACTOR_BUDGET,
            "low_actor_maximum": LOW_ACTOR_BUDGET,
            "vat": "represented - high_actor - low_actor",
            "lod_total": target,
        },
    )

    missing = missing_getter_names(state)
    add_check(
        checks,
        "complete_reflected_getter_contract",
        not missing,
        {"missing": missing, "queried_count": len(BASE_GETTERS) + len(CENTRAL_GETTERS)},
        {"missing": [], "queried_count": len(BASE_GETTERS) + len(CENTRAL_GETTERS)},
    )

    moving = metrics.get("central_moving")
    expected_moving = metrics.get("central_expected_moving")
    stuck = metrics.get("central_stuck")
    moving_values = post_warmup_values(state, "central_moving")
    stuck_values = post_warmup_values(state, "central_stuck")
    minimum_moving = min(moving_values) if moving_values else None
    maximum_stuck = max(stuck_values) if stuck_values else None
    moving_ratio = (
        minimum_moving / target if target and minimum_moving is not None else None
    )
    stuck_ratio = (
        maximum_stuck / target if target and maximum_stuck is not None else None
    )
    movement_actual = {
        "final_moving": moving,
        "final_expected_moving": expected_moving,
        "final_stuck": stuck,
        "post_warmup_minimum_moving": minimum_moving,
        "post_warmup_maximum_stuck": maximum_stuck,
        "post_warmup_sample_count": min(len(moving_values), len(stuck_values)),
        "population": target,
        "minimum_moving_ratio": moving_ratio,
        "maximum_stuck_ratio": stuck_ratio,
        "warmup_seconds": MOVEMENT_TELEMETRY_WARMUP_SECONDS,
    }
    if target == 300:
        add_check(
            checks,
            "gate300_sustains_at_least_95_percent_moving",
            moving_ratio is not None and moving_ratio >= MINIMUM_MOVING_RATIO_300,
            movement_actual,
            {"minimum_ratio": MINIMUM_MOVING_RATIO_300, "minimum_moving": 285},
        )
        add_check(
            checks,
            "gate300_sustains_less_than_2_percent_stuck",
            stuck_ratio is not None and stuck_ratio < MAXIMUM_STUCK_RATIO_300,
            movement_actual,
            {"strictly_less_than_ratio": MAXIMUM_STUCK_RATIO_300},
        )
        p95 = metrics.get("central_frame_time_p95_ms")
        frame_samples = metrics.get("central_frame_time_samples")
        frame_window = metrics.get("central_frame_time_window_seconds")
        add_check(
            checks,
            "gate300_p95_frame_time_under_33ms",
            p95 is not None
            and 0.0 < p95 < MAXIMUM_P95_FRAME_MS_300
            and (frame_samples or 0) > 0
            and (frame_window or 0.0) >= MINIMUM_FRAME_WINDOW_SECONDS_300,
            {
                "p50_ms": metrics.get("central_frame_time_p50_ms"),
                "p95_ms": p95,
                "maximum_ms": metrics.get("central_frame_time_maximum_ms"),
                "sample_count": frame_samples,
                "window_seconds": frame_window,
            },
            {
                "p95_strictly_less_than_ms": MAXIMUM_P95_FRAME_MS_300,
                "minimum_window_seconds": MINIMUM_FRAME_WINDOW_SECONDS_300,
            },
        )
    else:
        add_check(
            checks,
            "lower_gate_sustains_movement_and_stuck_health",
            moving_ratio is not None
            and moving_ratio >= MINIMUM_MOVING_RATIO_300
            and stuck_ratio is not None
            and stuck_ratio < MAXIMUM_STUCK_RATIO_300,
            movement_actual,
            {
                "minimum_moving_ratio": MINIMUM_MOVING_RATIO_300,
                "maximum_stuck_ratio_strictly_less_than": MAXIMUM_STUCK_RATIO_300,
            },
        )

    add_check(
        checks,
        "telemetry_observed_during_sample",
        (metrics.get("central_telemetry_observations") or 0) > 0
        and len(samples) >= 2,
        {
            "cpp_observations": metrics.get("central_telemetry_observations"),
            "verifier_samples": len(samples),
        },
        {"cpp_observations_minimum": 1, "verifier_samples_minimum": 2},
    )

    signal_scene = state.get("signal_scene") or {}
    add_check(
        checks,
        "signal_sources_preserved_30",
        signal_scene.get("source_actor_count") == EXPECTED_SIGNAL_SOURCES
        and signal_scene.get("unique_source_label_count") == EXPECTED_SIGNAL_SOURCES,
        {
            "actors": signal_scene.get("source_actor_count"),
            "unique_labels": signal_scene.get("unique_source_label_count"),
        },
        {"exact": EXPECTED_SIGNAL_SOURCES},
    )
    add_check(
        checks,
        "signal_ray_geometry_preserved_1920",
        signal_scene.get("ray_actor_count") == EXPECTED_SIGNAL_RAY_GEOMETRIES
        and signal_scene.get("unique_ray_label_count")
        == EXPECTED_SIGNAL_RAY_GEOMETRIES
        and signal_scene.get("ray_geometry_count")
        == EXPECTED_SIGNAL_RAY_GEOMETRIES
        and signal_scene.get("ray_labels_without_visible_geometry_count") == 0,
        {
            "ray_actors": signal_scene.get("ray_actor_count"),
            "unique_labels": signal_scene.get("unique_ray_label_count"),
            "visible_geometries": signal_scene.get("ray_geometry_count"),
            "assigned_mesh_components": signal_scene.get(
                "assigned_mesh_component_count"
            ),
            "visible_mesh_components": signal_scene.get(
                "visible_mesh_component_count"
            ),
            "labels_without_visible_geometry": signal_scene.get(
                "ray_labels_without_visible_geometry_count"
            ),
        },
        {"exact": EXPECTED_SIGNAL_RAY_GEOMETRIES},
    )
    color_counts = signal_scene.get("color_geometry_counts") or {}
    for color in SIGNAL_COLORS:
        add_check(
            checks,
            "signal_color_{}_preserved_480".format(color.lower()),
            color_counts.get(color) == EXPECTED_SIGNAL_GEOMETRIES_PER_COLOR,
            color_counts.get(color),
            {"exact": EXPECTED_SIGNAL_GEOMETRIES_PER_COLOR},
        )
    return checks


def write_report(report, target):
    report_dir = Path(
        unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_saved_dir())
    ) / "Reports" / "OpenMassCrowd"
    report_dir.mkdir(parents=True, exist_ok=True)
    gate_suffix = str(target) if target in ALLOWED_GATES else "unknown"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    timestamped = report_dir / "{}_gate{}_{}.json".format(
        REPORT_PREFIX, gate_suffix, stamp
    )
    gate_latest = report_dir / "{}_gate{}_latest.json".format(
        REPORT_PREFIX, gate_suffix
    )
    latest = report_dir / "{}_latest.json".format(REPORT_PREFIX)
    text = (
        json.dumps(
            report,
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    for path in (timestamped, gate_latest, latest):
        temporary = path.with_suffix(path.suffix + ".tmp")
        for candidate in (path, temporary):
            if candidate.exists() and not (candidate.stat().st_mode & stat.S_IWRITE):
                os.chmod(candidate, candidate.stat().st_mode | stat.S_IWRITE)
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, path)
    return timestamped, gate_latest, latest


def finalize(state, reason, world=None):
    if state.get("finished"):
        return
    state["finished"] = True
    handle = state.get("handle")
    if handle is not None:
        try:
            unreal.unregister_slate_post_tick_callback(handle)
        except Exception as error:
            append_error(state, "unregister_callback", error)
    setattr(builtins, CALLBACK_KEY, None)

    final_snapshot = state.get("latest_snapshot")
    if world is not None:
        try:
            final_snapshot = collect_snapshot(world, state)
            state["latest_snapshot"] = final_snapshot
        except Exception as error:
            append_error(state, "final_snapshot", error)

    if state.get("sample_started_game_seconds") is not None and world is not None:
        try:
            state["sample_elapsed_game_seconds"] = max(
                0.0,
                float(unreal.GameplayStatics.get_time_seconds(world))
                - state["sample_started_game_seconds"],
            )
        except Exception as error:
            append_error(state, "final_game_elapsed", error)
    if state.get("sample_started_monotonic") is not None:
        state["sample_elapsed_wall_seconds"] = max(
            0.0, time.monotonic() - state["sample_started_monotonic"]
        )

    if world is not None:
        try:
            state["signal_scene"] = collect_signal_scene(world, state)
        except Exception as error:
            append_error(state, "collect_signal_scene", error)
            state["signal_scene"] = None

    checks = build_checks(state, reason, final_snapshot)
    required_failures = [
        name
        for name, check in checks.items()
        if check["required"] and not check["passed"]
    ]
    advisory_failures = [
        name
        for name, check in checks.items()
        if not check["required"] and not check["passed"]
    ]
    overall_passed = bool(
        reason == "completed"
        and not required_failures
        and not state.get("errors")
    )
    target = state.get("target")
    report = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "generated_utc": utc_now(),
        "engine_version": unreal.SystemLibrary.get_engine_version(),
        "project_dir": unreal.Paths.convert_relative_path_to_full(
            unreal.Paths.project_dir()
        ),
        "completion_reason": reason,
        "overall_passed": overall_passed,
        "target_gate": target,
        "target_source": state.get("target_source"),
        "does_not_start_or_stop_pie": True,
        "collision_contract": {
            "pedestrian_radius_cm": 27.0,
            "severe_overlap_threshold_cm": SEVERE_OVERLAP_THRESHOLD_CM,
            "planned_spawn_quality_target_cm": (
                PLANNED_SPAWN_CLEARANCE_QUALITY_TARGET_CM
            ),
            "planned_spawn_quality_is_advisory": True,
        },
        "required_failed_checks": required_failures,
        "advisory_failed_checks": advisory_failures,
        "checks": checks,
        "getter_contract": {
            "base": BASE_GETTERS,
            "central": CENTRAL_GETTERS,
            "missing": missing_getter_names(state),
        },
        "sampling": {
            "required_seconds": SAMPLE_SECONDS_BY_GATE.get(target),
            "elapsed_game_seconds": round(
                state.get("sample_elapsed_game_seconds", 0.0), 3
            ),
            "elapsed_wall_seconds": round(
                state.get("sample_elapsed_wall_seconds", 0.0), 3
            ),
            "sample_interval_seconds": EVIDENCE_INTERVAL_SECONDS,
            "sample_count": len(state.get("samples", [])),
            "samples": state.get("samples", []),
        },
        "runtime_snapshot_at_sample_start": state.get("initial_snapshot"),
        "runtime_snapshot_final": final_snapshot,
        "network_asset_audit": state.get("network_asset_audit"),
        "signal_scene": state.get("signal_scene"),
        "diagnostics": {
            "error_count": len(state.get("errors", [])),
            "errors": state.get("errors", []),
            "status_updates": state.get("status_updates", []),
        },
    }

    try:
        paths = write_report(report, target)
        marker = {
            "overall_passed": overall_passed,
            "completion_reason": reason,
            "target_gate": target,
            "required_failed_checks": required_failures,
            "advisory_failed_checks": advisory_failures,
            "error_count": len(state.get("errors", [])),
            "timestamped_report": str(paths[0]),
            "gate_latest_report": str(paths[1]),
            "latest_report": str(paths[2]),
        }
        message = "OPEN_MASS_CENTRAL_GATE_RUNTIME_VERIFY=" + json.dumps(
            marker, ensure_ascii=False, sort_keys=True
        )
        if overall_passed:
            unreal.log_warning(message)
        else:
            unreal.log_error(message)
    except Exception as error:
        unreal.log_error(
            "OPEN_MASS_CENTRAL_GATE_REPORT_WRITE_FAILED=" + repr(error)
        )


def tick_verifier(delta_seconds):
    del delta_seconds
    state = getattr(builtins, STATE_KEY, None)
    if state is None or state.get("finished"):
        return
    now = time.monotonic()
    if now - state["last_poll_monotonic"] < POLL_INTERVAL_SECONDS:
        return
    state["last_poll_monotonic"] = now

    world = None
    try:
        world = get_pie_world()
        if world is None:
            if state.get("pie_seen"):
                add_status(state, "pie_ended_during_verification")
                finalize(state, "pie_ended_early", None)
                return
            add_status(state, "waiting_for_existing_pie_world")
        else:
            state["pie_seen"] = True
            snapshot = collect_snapshot(world, state)
            state["latest_snapshot"] = snapshot
            inferred = infer_target(snapshot)
            if state.get("target") is None and inferred is not None:
                state["target"] = inferred
                state["target_source"] = "runtime_spawner"

            target = state.get("target")
            if (
                state.get("explicit_target") is not None
                and inferred is not None
                and inferred != state["explicit_target"]
            ):
                if state.get("gate_mismatch_started") is None:
                    state["gate_mismatch_started"] = now
                add_status(state, "configured_gate_mismatch", snapshot)
                if now - state["gate_mismatch_started"] >= GATE_MISMATCH_GRACE_SECONDS:
                    finalize(state, "configured_gate_mismatch", world)
                    return
            else:
                state["gate_mismatch_started"] = None

            if target is None:
                add_status(state, "waiting_to_infer_population_gate", snapshot)
            elif not snapshot_ready(snapshot, target):
                if state.get("sample_started_game_seconds") is not None:
                    add_status(state, "population_or_network_changed_during_sample", snapshot)
                    finalize(state, "runtime_changed_during_sample", world)
                    return
                add_status(state, "waiting_for_exact_admission_and_six_district_network", snapshot)
            else:
                game_seconds = float(unreal.GameplayStatics.get_time_seconds(world))
                if state.get("sample_started_game_seconds") is None:
                    state["sample_started_game_seconds"] = game_seconds
                    state["sample_started_monotonic"] = now
                    state["last_evidence_game_seconds"] = game_seconds
                    state["initial_snapshot"] = snapshot
                    state["samples"].append(sample_summary(snapshot, 0.0, 0.0))
                    add_status(state, "sampling_gate{}".format(target), snapshot)
                else:
                    elapsed_game = max(
                        0.0, game_seconds - state["sample_started_game_seconds"]
                    )
                    elapsed_wall = max(
                        0.0, now - state["sample_started_monotonic"]
                    )
                    state["sample_elapsed_game_seconds"] = elapsed_game
                    state["sample_elapsed_wall_seconds"] = elapsed_wall
                    if (
                        game_seconds - state["last_evidence_game_seconds"]
                        >= EVIDENCE_INTERVAL_SECONDS
                    ):
                        state["last_evidence_game_seconds"] = game_seconds
                        state["samples"].append(
                            sample_summary(snapshot, elapsed_game, elapsed_wall)
                        )
                    required = SAMPLE_SECONDS_BY_GATE[target]
                    if elapsed_game >= required and elapsed_wall >= required:
                        if not state["samples"] or (
                            elapsed_game
                            - state["samples"][-1]["elapsed_game_seconds"]
                            > 0.05
                        ):
                            state["samples"].append(
                                sample_summary(snapshot, elapsed_game, elapsed_wall)
                            )
                        add_status(state, "finalizing_gate{}_evidence".format(target), snapshot)
                        finalize(state, "completed", world)
                        return

        if now - state["started_monotonic"] >= WAIT_TIMEOUT_SECONDS:
            add_status(state, "verification_timeout", state.get("latest_snapshot"))
            finalize(state, "timeout", world)
    except Exception as error:
        append_error(state, "tick_verifier", error)
        finalize(state, "callback_exception", world)


def main():
    old_handle = getattr(builtins, CALLBACK_KEY, None)
    if old_handle is not None:
        try:
            unreal.unregister_slate_post_tick_callback(old_handle)
        except Exception:
            pass

    explicit_raw = gate_argument_raw(sys.argv[1:])
    explicit_source = "script_argument" if explicit_raw is not None else None
    if explicit_raw is None:
        explicit_raw = os.environ.get("OPEN_MASS_CENTRAL_GATE")
        explicit_source = "environment" if explicit_raw is not None else None
    if explicit_raw is None:
        explicit_raw = os.environ.get("OPEN_MASS_CROWD_GATE")
        explicit_source = "environment" if explicit_raw is not None else None
    explicit_target = parse_gate(explicit_raw)
    if explicit_raw is not None and explicit_target is None:
        unreal.log_error(
            "OPEN_MASS_CENTRAL_GATE_VERIFY_INVALID_GATE={!r}; expected one of {}".format(
                explicit_raw, ALLOWED_GATES
            )
        )

    now = time.monotonic()
    state = {
        "started_monotonic": now,
        "last_poll_monotonic": 0.0,
        "target": explicit_target,
        "explicit_target": explicit_target,
        "target_source": explicit_source if explicit_target is not None else None,
        "gate_mismatch_started": None,
        "sample_started_game_seconds": None,
        "sample_started_monotonic": None,
        "sample_elapsed_game_seconds": 0.0,
        "sample_elapsed_wall_seconds": 0.0,
        "last_evidence_game_seconds": None,
        "latest_snapshot": None,
        "initial_snapshot": None,
        "network_asset_audit": None,
        "samples": [],
        "status_updates": [],
        "errors": [],
        "pie_seen": False,
        "finished": False,
        "handle": None,
    }
    if explicit_raw is not None and explicit_target is None:
        append_error(
            state,
            "configuration.gate",
            ValueError("invalid gate {!r}; expected {}".format(explicit_raw, ALLOWED_GATES)),
        )
    state["handle"] = unreal.register_slate_post_tick_callback(tick_verifier)
    setattr(builtins, CALLBACK_KEY, state["handle"])
    setattr(builtins, STATE_KEY, state)
    unreal.log_warning(
        "OPEN_MASS_CENTRAL_GATE_RUNTIME_VERIFY_STARTED="
        + json.dumps(
            {
                "requested_gate": explicit_target,
                "target_source": state["target_source"] or "runtime_inference",
                "allowed_gates": list(ALLOWED_GATES),
                "sample_seconds_by_gate": SAMPLE_SECONDS_BY_GATE,
                "wait_timeout_seconds": WAIT_TIMEOUT_SECONDS,
                "does_not_start_or_stop_pie": True,
                "queried_getter_count": len(BASE_GETTERS) + len(CENTRAL_GETTERS),
            },
            sort_keys=True,
        )
    )


def self_test():
    assert parse_gate(30) == 30
    assert parse_gate("Gate100") == 100
    assert parse_gate("EOpenMassCrowdCentralPopulationGate.GATE200") == 200
    assert parse_gate("<OpenMassCrowdCentralPopulationGate.GATE30: 0>") == 30
    assert parse_gate("<OpenMassCrowdCentralPopulationGate.GATE300: 3>") == 300
    assert parse_gate("central_population_gate_300") == 300
    assert parse_gate(True) is None
    assert parse_gate(999) is None
    assert parse_gate("nonsense") is None
    assert overlap_window_passes(7, 7, 0, 0)
    assert not overlap_window_passes(7, 8, 0, 0)
    assert not overlap_window_passes(7, 7, 1, 2)
    assert center_clearance_passes(20.0, 20.0)
    assert center_clearance_passes(25.0, 20.0)
    assert not center_clearance_passes(19.99, 25.0)
    assert not center_clearance_passes(None, 20.0)
    assert gate_argument_raw(["--gate", "100"]) == "100"
    assert gate_argument_raw(["--other", "x", "--gate=200"]) == "200"
    assert gate_argument_raw(["--gate"]) == ""
    assert gate_argument_raw(["--other", "300"]) is None
    assert canonical_world_package(
        "/Game/Maps/UEDPIE_0_shanghai.shanghai:PersistentLevel"
    ) == EXPECTED_MAP_PACKAGE
    assert canonical_world_package(
        "/Game/Maps/UEDPIE_12_shanghai.UEDPIE_12_shanghai"
    ) == EXPECTED_MAP_PACKAGE
    assert canonical_world_package("/Game/Maps/not_shanghai.not_shanghai") != (
        EXPECTED_MAP_PACKAGE
    )
    assert json_safe(float("nan")) is None
    assert json_safe(float("inf")) is None
    synthetic_state = {
        "samples": [
            {
                "elapsed_game_seconds": 0.0,
                "metrics": {"central_moving": 0, "central_stuck": 0},
            },
            {
                "elapsed_game_seconds": MOVEMENT_TELEMETRY_WARMUP_SECONDS,
                "metrics": {"central_moving": 29, "central_stuck": 0},
            },
            {
                "elapsed_game_seconds": MOVEMENT_TELEMETRY_WARMUP_SECONDS + 1.0,
                "metrics": {"central_moving": 30, "central_stuck": 0},
            },
        ]
    }
    assert post_warmup_values(synthetic_state, "central_moving") == [29, 30]
    assert min(post_warmup_values(synthetic_state, "central_moving")) == 29
    assert bounded_batch_admission_passes(30, 25, 25, 0.1, 2)
    assert bounded_batch_admission_passes(30, 25, 15, 0.1, 2)
    assert bounded_batch_admission_passes(30, 25, 10, 0.1, 3)
    assert not bounded_batch_admission_passes(30, 25, 14, 0.1, 2)
    assert not bounded_batch_admission_passes(30, 25, 26, 0.1, 2)
    assert not bounded_batch_admission_passes(30, 25, 25, 0.1, 1)
    assert not bounded_batch_admission_passes(30, 25, 25, 0.001, 2)
    assert len(CENTRAL_GETTERS) == 40
    assert len(set(CENTRAL_GETTERS.values())) == len(CENTRAL_GETTERS)
    assert set(SAMPLE_SECONDS_BY_GATE) == set(ALLOWED_GATES)
    assert all(
        SAMPLE_SECONDS_BY_GATE[gate] >= 60.0 for gate in ALLOWED_GATES
    )
    print(
        json.dumps(
            {
                "status": "PASS",
                "allowed_gates": list(ALLOWED_GATES),
                "base_getters": len(BASE_GETTERS),
                "central_getters": len(CENTRAL_GETTERS),
                "sample_seconds_by_gate": SAMPLE_SECONDS_BY_GATE,
            },
            sort_keys=True,
        )
    )


if "--self-test" in sys.argv:
    self_test()
elif unreal is None:
    raise SystemExit(
        "Run this verifier inside Unreal Editor, or pass --self-test on the host."
    )
else:
    main()
