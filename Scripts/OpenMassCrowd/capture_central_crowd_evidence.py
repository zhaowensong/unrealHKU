"""Capture auditable screenshots from an already-running Central crowd PIE world.

The helper never starts/stops PIE and never changes the map, spawner, network,
population, or Mass entities.  A camera preset changes only the editor viewport.
Crowd presets temporarily hide ``SIG_Ray_*``/``SIG_Source_*`` runtime actors so
the crowd is not obscured; every prior hidden state is verified and restored
before a capture can pass.  ``signal-regression`` is the separate, unhidden
telecom proof.  Route and crossing markers are transient debug draws; LOD
captures deliberately have no marker that could cover the target person.

Example (run from the project root while PIE is active)::

    python Scripts/OpenMassCrowd/run_unreal_python_via_mcp.py \
      --file Scripts/OpenMassCrowd/capture_central_crowd_evidence.py \
      --script-arg=--label --script-arg=gate300-wide \
      --script-arg=--preset --script-arg=wide

Timestamped and label-latest PNG/JSON files are written under
``Saved/Reports/OpenMassCrowd/Central300``.  The latest files are updated only
after the screenshot is a stable, valid 1600x900 PNG.

The ``final`` suite captures wide, street grounding, route overlay, crossing,
High/Low/VAT LOD, telemetry context, and signal regression in one MCP call::

    python Scripts/OpenMassCrowd/run_unreal_python_via_mcp.py \
      --file Scripts/OpenMassCrowd/capture_central_crowd_evidence.py \
      --script-arg=--label --script-arg=central-300-final \
      --script-arg=--suite --script-arg=final

No screenshot is claimed to prove telemetry text.  The telemetry preset records
the authoritative reflected runtime counters in JSON and labels its PNG as
visual context only because this helper does not inject a HUD.
"""

from __future__ import annotations

import argparse
import builtins
import json
import math
import os
import re
import shutil
import stat
import struct
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    import unreal
except ImportError:  # Host-only self-test.
    unreal = None


PRESETS = (
    "wide",
    "street",
    "route-overlay",
    "crossing",
    "lod",
    "lod-high",
    "lod-low",
    "lod-vat",
    "telemetry",
    "signal-regression",
)
SUITES = {
    "final": (
        "wide",
        "street",
        "route-overlay",
        "crossing",
        "lod-high",
        "lod-low",
        "lod-vat",
        "telemetry",
        "signal-regression",
    ),
}
GATES = (30, 100, 200, 300)
WIDTH = 1600
HEIGHT = 900
TIMEOUT_SECONDS = 45.0
SETTLE_SECONDS = 0.4
OVERLAY_DURATION_SECONDS = 2.0
OVERLAY_CLEARANCE_SECONDS = 0.35
MAX_ROUTE_OVERLAY_SEGMENTS = 4500
CALLBACK_KEY = "_hk_central_evidence_capture_handle"
STATE_KEY = "_hk_central_evidence_capture_state"
PREFIX = "central_crowd_evidence"
SUITE_PREFIX = "central_crowd_evidence_suite"
SIGNAL_COLORS = ("Green", "Yellow", "Orange", "Red")
SIGNAL_SOURCE_PATTERN = re.compile(r"^SIG_Source_\d{2}_Direct_Roof$")
SIGNAL_RAY_PATTERN = re.compile(
    r"^SIG_Ray_\d{3}_(?:Segment|RoofHit)_\d{2}_"
    r"(Green|Yellow|Orange|Red)$"
)
SIGNAL_EXPECTED_SOURCES = 30
SIGNAL_EXPECTED_GEOMETRIES = 1920
SIGNAL_EXPECTED_PER_COLOR = 480
REQUIRED_GATE_RUNTIME_REPORT_SCHEMA_VERSION = 4
REQUIRED_COLLISION_CHECK_NAME = (
    "no_severe_overlap_below_20cm_during_steady_60s_window"
)
SEVERE_OVERLAP_THRESHOLD_CM = 20.0
PLANNED_CENTER_CLEARANCE_QUALITY_TARGET_CM = 55.0
SIGNAL_HIDDEN_PRESETS = frozenset(
    preset for preset in PRESETS if preset != "signal-regression"
)
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
MISSING = object()
RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *("COM{}".format(index) for index in range(1, 10)),
    *("LPT{}".format(index) for index in range(1, 10)),
}


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def safe_label(value):
    text = str(value or "").strip()
    result = re.sub(r"[^\w.-]+", "-", text, flags=re.UNICODE).strip(" .-_")
    result = re.sub(r"[-_]{2,}", "-", result)[:80].rstrip(" .-_")
    if not result or result.upper() in RESERVED_NAMES:
        raise ValueError("evidence label is empty or not a safe Windows filename")
    return result


def parse_gate(value):
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        candidate = int(value)
        return candidate if candidate in GATES else None
    normalized = str(value).strip().upper().replace("_", "").replace("-", "")
    for gate in reversed(GATES):
        if normalized == str(gate) or normalized.endswith("GATE{}".format(gate)):
            return gate
    return None


def path_of(value):
    if value is None:
        return None
    try:
        return value.get_path_name()
    except Exception:
        return str(value)


def json_value(value):
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if value is None or isinstance(value, (bool, int, str)):
        return value
    return path_of(value)


def prop(value, name, default=MISSING):
    try:
        return value.get_editor_property(name)
    except Exception as first_error:
        try:
            return getattr(value, name)
        except Exception:
            if default is MISSING:
                raise first_error
            return default


def optional_call(value, name):
    try:
        return json_value(getattr(value, name)())
    except Exception:
        return None


def network_mode(spawner):
    return optional_call(spawner, "get_network_mode") or json_value(
        prop(spawner, "network_mode")
    )


def central_mode(value):
    compact = str(value).upper().replace("_", "").replace("-", "")
    if "CENTRALCERTIFIEDCACHE" in compact:
        return True
    try:
        return int(value) == 1
    except (TypeError, ValueError):
        return False


def select_spawner(spawners):
    ordered = sorted(spawners, key=lambda item: path_of(item) or "")
    if len(ordered) != 1:
        raise RuntimeError(
            "PIE requires exactly one OpenMassCrowdSpawner; found {}: {}".format(
                len(ordered), [path_of(item) for item in ordered]
            )
        )
    spawner = ordered[0]
    if not central_mode(network_mode(spawner)):
        raise RuntimeError("the unique runtime spawner is not in Central mode")
    if prop(spawner, "central_network_asset", default=None) is None:
        raise RuntimeError("the unique Central spawner has no network asset")
    return spawner


def pie_world(value):
    if value is None:
        return False
    try:
        if "PIE" in str(value.get_world_type()).upper():
            return True
    except Exception:
        pass
    return "UEDPIE_" in (path_of(value) or "").upper()


def png_dimensions_from_header(header):
    if len(header) < 24 or header[:8] != PNG_SIGNATURE or header[12:16] != b"IHDR":
        raise ValueError("screenshot is not a valid PNG")
    return struct.unpack(">II", header[16:24])


def png_dimensions(path):
    with path.open("rb") as stream:
        return png_dimensions_from_header(stream.read(24))


def output_paths(output_dir, label, stamp):
    stamped = "{}_{}_{}".format(PREFIX, label, stamp)
    latest = "{}_{}_latest".format(PREFIX, label)
    return {
        "png": output_dir / (stamped + ".png"),
        "latest_png": output_dir / (latest + ".png"),
        "json": output_dir / (stamped + ".json"),
        "latest_json": output_dir / (latest + ".json"),
    }


def suite_output_paths(output_dir, label, stamp):
    stamped = "{}_{}_{}".format(SUITE_PREFIX, label, stamp)
    latest = "{}_{}_latest".format(SUITE_PREFIX, label)
    return {
        "json": output_dir / (stamped + ".json"),
        "latest_json": output_dir / (latest + ".json"),
    }


def make_writable(path):
    if path.exists() and not (path.stat().st_mode & stat.S_IWRITE):
        os.chmod(path, path.stat().st_mode | stat.S_IWRITE)


def atomic_json(path, payload):
    temporary = path.with_suffix(path.suffix + ".tmp")
    make_writable(path)
    make_writable(temporary)
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def atomic_copy(source, destination):
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    make_writable(destination)
    make_writable(temporary)
    shutil.copy2(source, temporary)
    os.replace(temporary, destination)


def report_dir():
    saved = unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_saved_dir())
    return Path(saved) / "Reports" / "OpenMassCrowd" / "Central300"


def runtime_objects():
    subsystem = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem)
    world = subsystem.get_game_world()
    if not pie_world(world):
        raise RuntimeError("no existing PIE world is active; this script never starts PIE")
    actor_class = getattr(unreal, "OpenMassCrowdSpawner", None)
    if actor_class is None:
        raise RuntimeError("OpenMassCrowdSpawner is not reflected; rebuild/restart UE")
    spawners = unreal.GameplayStatics.get_all_actors_of_class(world, actor_class)
    return world, select_spawner(list(spawners))


def runtime_snapshot(world, spawner):
    gate_property = prop(spawner, "central_population_gate", default=None)
    population_property = prop(spawner, "population_count", default=None)
    metrics = {
        "spawned": optional_call(spawner, "get_spawned_entity_count"),
        "runtime_lanes": optional_call(spawner, "get_runtime_lane_count"),
        "runtime_nodes": optional_call(spawner, "get_runtime_network_node_count"),
        "route_assignments": optional_call(spawner, "get_route_assignment_count"),
        "completed_trips": optional_call(spawner, "get_completed_trip_count"),
        "short_path_chunks": optional_call(
            spawner, "get_central_short_path_chunk_count"
        ),
        "route_replans": optional_call(spawner, "get_route_replan_count"),
        "ground_projection_failures": optional_call(
            spawner, "get_ground_projection_failure_count"
        ),
        "ground_rollbacks": optional_call(spawner, "get_ground_rollback_count"),
        "ground_center_recoveries": optional_call(
            spawner, "get_ground_center_recovery_count"
        ),
        "ground_unrecoverable": optional_call(
            spawner, "get_ground_unrecoverable_count"
        ),
        "admission_target": optional_call(
            spawner, "get_central_admission_target_count"
        ),
        "admitted": optional_call(spawner, "get_central_admitted_entity_count"),
        "admission_batches": optional_call(
            spawner, "get_central_admission_batch_count"
        ),
        "planned_spawn_slots": optional_call(
            spawner, "get_central_planned_spawn_slot_count"
        ),
        "minimum_planned_spawn_clearance_cm": optional_call(
            spawner, "get_central_minimum_planned_spawn_clearance_cm"
        ),
        "simulated": optional_call(spawner, "get_central_simulated_entity_count"),
        "represented": optional_call(
            spawner, "get_central_represented_entity_count"
        ),
        "high_actor": optional_call(
            spawner, "get_central_high_actor_representation_count"
        ),
        "low_actor": optional_call(
            spawner, "get_central_low_actor_representation_count"
        ),
        "vat": optional_call(spawner, "get_central_vat_representation_count"),
        "actor_total": optional_call(
            spawner, "get_central_actor_representation_count"
        ),
        "expected_moving": optional_call(
            spawner, "get_central_expected_moving_entity_count"
        ),
        "moving": optional_call(spawner, "get_central_moving_entity_count"),
        "stuck": optional_call(spawner, "get_central_stuck_entity_count"),
        "severe_overlap_pairs": optional_call(
            spawner, "get_central_severe_overlap_pair_count"
        ),
        "severe_overlap_agents": optional_call(
            spawner, "get_central_severe_overlap_agent_count"
        ),
        "peak_severe_overlap_pairs": optional_call(
            spawner, "get_central_peak_severe_overlap_pair_count"
        ),
        "peak_severe_overlap_agents": optional_call(
            spawner, "get_central_peak_severe_overlap_agent_count"
        ),
        "severe_overlap_pair_observations": optional_call(
            spawner, "get_central_severe_overlap_pair_observation_count"
        ),
        "minimum_center_distance_cm": optional_call(
            spawner, "get_central_minimum_entity_center_distance_cm"
        ),
        "minimum_observed_center_distance_cm": optional_call(
            spawner, "get_central_minimum_observed_entity_center_distance_cm"
        ),
        "unsupported_visuals": optional_call(
            spawner, "get_current_unsupported_visual_count"
        ),
        "ground_guard_queries": optional_call(
            spawner, "get_central_ground_guard_query_count"
        ),
        "ground_candidate_component_tests": optional_call(
            spawner, "get_central_ground_candidate_component_test_count"
        ),
        "ground_component_cache_refreshes": optional_call(
            spawner, "get_central_ground_component_cache_refresh_count"
        ),
        "frame_p50_ms": optional_call(spawner, "get_central_frame_time_p50_ms"),
        "frame_p95_ms": optional_call(spawner, "get_central_frame_time_p95_ms"),
        "frame_maximum_ms": optional_call(
            spawner, "get_central_frame_time_maximum_ms"
        ),
        "frame_sample_count": optional_call(
            spawner, "get_central_frame_time_sample_count"
        ),
        "frame_window_seconds": optional_call(
            spawner, "get_central_frame_time_window_seconds"
        ),
        "telemetry_observations": optional_call(
            spawner, "get_central_telemetry_observation_count"
        ),
    }
    gate = parse_gate(gate_property) or parse_gate(metrics["admission_target"])
    gate = gate or parse_gate(population_property)
    population = metrics["simulated"]
    population_source = "central_simulated"
    if population is None:
        population, population_source = metrics["spawned"], "spawned"
    if population is None:
        population, population_source = population_property, "population_property"
    return {
        "world": path_of(world),
        "world_name": world.get_name(),
        "pie_proof": True,
        "spawner": path_of(spawner),
        "spawner_count": 1,
        "network_mode": network_mode(spawner),
        "network_asset": path_of(prop(spawner, "central_network_asset")),
        "gate": gate,
        "gate_property": json_value(gate_property),
        "population": json_value(population),
        "population_source": population_source,
        "population_property": json_value(population_property),
        "metrics": metrics,
    }


def vector_json(value):
    return {"x": float(value.x), "y": float(value.y), "z": float(value.z)}


def rotator_json(value):
    return {
        "pitch": float(value.pitch),
        "yaw": float(value.yaw),
        "roll": float(value.roll),
    }


def text_of(value):
    if value is None:
        return ""
    try:
        return value.to_string()
    except Exception:
        return str(value)


def actor_label(actor):
    try:
        return str(actor.get_actor_label())
    except Exception:
        try:
            return str(actor.get_name())
        except Exception:
            return path_of(actor) or ""


def actor_hidden(actor):
    try:
        return bool(actor.is_hidden())
    except Exception:
        return bool(prop(actor, "hidden", default=False))


def xy_distance_squared(first, second):
    dx = float(first.x) - float(second.x)
    dy = float(first.y) - float(second.y)
    return dx * dx + dy * dy


def normalized_xy(first, second):
    dx = float(second.x) - float(first.x)
    dy = float(second.y) - float(first.y)
    length = math.hypot(dx, dy)
    if length <= 0.001:
        return unreal.Vector(1.0, 0.0, 0.0)
    return unreal.Vector(dx / length, dy / length, 0.0)


def signal_label_kind(label):
    label = str(label or "")
    if SIGNAL_SOURCE_PATTERN.fullmatch(label):
        return "source", None
    match = SIGNAL_RAY_PATTERN.fullmatch(label)
    if match is None:
        return None, None
    return "ray", match.group(1)


def visible_static_mesh_geometry_for_actor(actor):
    static_mesh_class = getattr(unreal, "StaticMeshComponent", None)
    ism_class = getattr(unreal, "InstancedStaticMeshComponent", None)
    if static_mesh_class is None:
        return 0, 0, 0
    geometry_count = 0
    assigned_component_count = 0
    visible_component_count = 0
    for component in actor.get_components_by_class(static_mesh_class):
        getter = getattr(component, "get_static_mesh", None)
        mesh = getter() if getter is not None else prop(
            component, "static_mesh", default=None
        )
        if mesh is None:
            continue
        assigned_component_count += 1
        hidden_in_game = bool(prop(component, "hidden_in_game", default=False))
        if not component.is_visible() or hidden_in_game or actor_hidden(actor):
            continue
        visible_component_count += 1
        if ism_class is not None and isinstance(component, ism_class):
            instance_getter = getattr(component, "get_instance_count", None)
            geometry_count += int(instance_getter()) if instance_getter else 0
        else:
            geometry_count += 1
    return geometry_count, assigned_component_count, visible_component_count


def signal_inventory(world, actors=None):
    actors = actors if actors is not None else list(
        unreal.GameplayStatics.get_all_actors_of_class(world, unreal.Actor)
    )
    sources = []
    rays = []
    source_labels = []
    ray_labels = []
    color_geometry_counts = {color: 0 for color in SIGNAL_COLORS}
    ray_geometry_count = 0
    assigned_mesh_component_count = 0
    visible_mesh_component_count = 0
    labels_without_visible_geometry = []
    for actor in actors:
        label = actor_label(actor)
        kind, color = signal_label_kind(label)
        if kind == "source":
            sources.append(actor)
            source_labels.append(label)
        elif kind == "ray":
            rays.append(actor)
            ray_labels.append(label)
            geometry, assigned, visible = visible_static_mesh_geometry_for_actor(actor)
            ray_geometry_count += geometry
            assigned_mesh_component_count += assigned
            visible_mesh_component_count += visible
            color_geometry_counts[color] += geometry
            if geometry <= 0:
                labels_without_visible_geometry.append(label)
    checks = {
        "source_actor_and_unique_label_count": (
            len(sources) == SIGNAL_EXPECTED_SOURCES
            and len(set(source_labels)) == SIGNAL_EXPECTED_SOURCES
        ),
        "ray_actor_unique_label_and_visible_geometry_count": (
            len(rays) == SIGNAL_EXPECTED_GEOMETRIES
            and len(set(ray_labels)) == SIGNAL_EXPECTED_GEOMETRIES
            and ray_geometry_count == SIGNAL_EXPECTED_GEOMETRIES
            and not labels_without_visible_geometry
        ),
        "per_color_visible_geometry_counts": all(
            color_geometry_counts[color] == SIGNAL_EXPECTED_PER_COLOR
            for color in SIGNAL_COLORS
        ),
    }
    payload = {
        "source_actor_count": len(sources),
        "unique_source_label_count": len(set(source_labels)),
        "ray_actor_count": len(rays),
        "unique_ray_label_count": len(set(ray_labels)),
        "ray_geometry_count": ray_geometry_count,
        "assigned_mesh_component_count": assigned_mesh_component_count,
        "visible_mesh_component_count": visible_mesh_component_count,
        "ray_labels_without_visible_geometry_count": len(
            labels_without_visible_geometry
        ),
        "ray_labels_without_visible_geometry_samples": sorted(
            labels_without_visible_geometry
        )[:40],
        "color_geometry_counts": color_geometry_counts,
        "expected": {
            "source_actor_count": SIGNAL_EXPECTED_SOURCES,
            "ray_geometry_count": SIGNAL_EXPECTED_GEOMETRIES,
            "color_geometry_counts": {
                color: SIGNAL_EXPECTED_PER_COLOR for color in SIGNAL_COLORS
            },
        },
        "checks": checks,
        "strict_visible_regression_pass": all(checks.values()),
        "actor_identity_source": (
            "Exact PIE labels matching the schema-4 verifier regex contract"
        ),
    }
    return payload, sources + rays, rays


def representation_tier(actor):
    raw = optional_call(actor, "get_representation_tier")
    upper = text_of(raw).upper()
    if "HIGH" in upper:
        return "High"
    if "LOW" in upper:
        return "Low"
    return "Unknown"


def collect_visible_proxies(world):
    actor_class = getattr(unreal, "OpenMassCrowdCitySampleActor", None)
    actors = list(
        unreal.GameplayStatics.get_all_actors_of_class(world, actor_class)
    ) if actor_class is not None else []
    result = []
    for actor in actors:
        if actor_hidden(actor):
            continue
        result.append({
            "actor": actor,
            "path": path_of(actor),
            "label": actor_label(actor),
            "location": actor.get_actor_location(),
            "tier": representation_tier(actor),
        })
    return sorted(result, key=lambda item: item["path"] or item["label"])


def bounds_info(asset):
    bounds = prop(asset, "world_bounds")
    minimum, maximum = prop(bounds, "min"), prop(bounds, "max")
    center = unreal.Vector(
        (float(minimum.x) + float(maximum.x)) * 0.5,
        (float(minimum.y) + float(maximum.y)) * 0.5,
        (float(minimum.z) + float(maximum.z)) * 0.5,
    )
    extent = max(
        abs(float(maximum.x) - float(minimum.x)) * 0.5,
        abs(float(maximum.y) - float(minimum.y)) * 0.5,
    )
    if extent <= 1.0:
        raise RuntimeError("Central network world bounds are empty")
    return {
        "minimum": minimum,
        "maximum": maximum,
        "center": center,
        "extent": extent,
    }


def pedestrian_class_key(value):
    upper = text_of(value).upper().replace("_", "")
    for key in ("CROSSING", "SIDEWALK", "FOOTWAY", "PEDESTRIANZONE"):
        if key in upper:
            return key.lower()
    if "MANUALPEDESTRIANLINK" in upper:
        return "manual_link"
    return "unknown"


def certified_lanes(asset, physical_once=False):
    result = []
    for cell in list(prop(asset, "cells", default=[])):
        cell_id = text_of(prop(cell, "cell_id", default=""))
        for lane in list(prop(cell, "directed_lanes", default=[])):
            if not bool(prop(lane, "certified", default=False)):
                continue
            lane_id = text_of(prop(lane, "lane_id", default=""))
            reverse_id = text_of(prop(lane, "reverse_lane_id", default=""))
            if physical_once and reverse_id and lane_id > reverse_id:
                continue
            samples = list(prop(lane, "ground_samples", default=[]))
            points = [
                prop(sample, "center_position")
                for sample in samples
                if prop(sample, "center_position", default=None) is not None
            ]
            if len(points) < 2:
                continue
            result.append({
                "object": lane,
                "lane_id": lane_id,
                "reverse_lane_id": reverse_id,
                "cell_id": cell_id,
                "pedestrian_class": pedestrian_class_key(
                    prop(lane, "pedestrian_class", default="")
                ),
                "length_cm": float(prop(lane, "length_cm", default=0.0)),
                "points": points,
            })
    return sorted(result, key=lambda item: item["lane_id"])


def lane_midpoint(lane):
    points = lane["points"]
    middle = points[len(points) // 2]
    return unreal.Vector(float(middle.x), float(middle.y), float(middle.z))


def nearest_lane(lanes, location):
    if not lanes:
        return None
    return min(
        lanes,
        key=lambda lane: (xy_distance_squared(lane_midpoint(lane), location), lane["lane_id"]),
    )


def dense_proxy_anchor(proxies, bounds_center, tier=None):
    candidates = [item for item in proxies if tier is None or item["tier"] == tier]
    if not candidates:
        return None, []
    radius_squared = 2500.0 * 2500.0
    scored = []
    for candidate in candidates:
        neighbors = [
            item for item in proxies
            if xy_distance_squared(item["location"], candidate["location"]) <= radius_squared
        ]
        scored.append((
            -len(neighbors),
            xy_distance_squared(candidate["location"], bounds_center),
            candidate["path"] or candidate["label"],
            candidate,
            neighbors,
        ))
    scored.sort(key=lambda item: item[:3])
    selected = scored[0]
    neighbors = sorted(
        selected[4],
        key=lambda item: (
            xy_distance_squared(item["location"], selected[3]["location"]),
            item["path"] or item["label"],
        ),
    )[:16]
    return selected[3], neighbors


def average_location(items):
    if not items:
        return None
    count = float(len(items))
    return unreal.Vector(
        sum(float(item["location"].x) for item in items) / count,
        sum(float(item["location"].y) for item in items) / count,
        sum(float(item["location"].z) for item in items) / count,
    )


def central_lod_identity_snapshot(spawner):
    raw = optional_call(spawner, "get_central_lod_evidence_snapshot")
    if not isinstance(raw, str) or not raw:
        raise RuntimeError("Central LOD identity snapshot is not reflected")
    snapshot = json.loads(raw)
    if not snapshot.get("valid"):
        raise RuntimeError(
            "Central LOD identity target is unavailable: {}".format(snapshot)
        )
    return snapshot


def snapshot_location(snapshot):
    point = snapshot["location"]
    return unreal.Vector(float(point["x"]), float(point["y"]), float(point["z"]))


def stable_snapshot_actor(actors, snapshot):
    expected_path = snapshot.get("actor_path")
    expected_seed = snapshot.get("appearance_seed")
    for actor in actors:
        if expected_path and expected_path != "None" and path_of(actor) == expected_path:
            return actor
    for actor in actors:
        seed = optional_call(actor, "get_mass_appearance_seed")
        if isinstance(seed, (int, float)) and int(seed) == int(expected_seed):
            return actor
    return None


def hit_property(hit, name, default=None):
    return prop(hit, name, default=default)


def clear_lod_camera(world, target, distance, height, ignored_actors):
    chest = target + unreal.Vector(0.0, 0.0, 95.0)
    ignored = [actor for actor in ignored_actors if actor is not None]
    tested = 0
    for height_scale in (1.0, 1.35, 1.7):
        for angle_index in range(24):
            angle = math.radians(angle_index * 15.0)
            candidate = target + unreal.Vector(
                math.cos(angle) * distance,
                math.sin(angle) * distance,
                height * height_scale,
            )
            tested += 1
            hit = unreal.SystemLibrary.line_trace_single(
                world,
                chest,
                candidate,
                unreal.TraceTypeQuery.TRACE_TYPE_QUERY1,
                True,
                ignored,
                unreal.DrawDebugTrace.NONE,
                True,
            )
            if not bool(hit_property(hit, "blocking_hit", False)):
                return candidate, {
                    "line_of_sight_clear": True,
                    "candidate_count_tested": tested,
                    "horizontal_distance_cm": float(distance),
                    "height_cm": float(height * height_scale),
                }
    raise RuntimeError(
        "no unobstructed LOD evidence camera found after {} candidates".format(
            tested
        )
    )


def transform_translation(value):
    candidates = value if isinstance(value, tuple) else (value,)
    for candidate in candidates:
        if candidate is None or isinstance(candidate, bool):
            continue
        location = prop(candidate, "translation", default=None)
        if location is not None:
            return location
    return None


def collect_vat_instances(actors):
    component_class = getattr(unreal, "InstancedStaticMeshComponent", None)
    if component_class is None:
        return {
            "component_count": 0,
            "part_instance_count": 0,
            "direct_target_available": False,
            "inspection_error": "InstancedStaticMeshComponent is not reflected",
        }, None, []
    components = []
    direct_target = None
    unique_positions = {}
    variant_ids = set()
    custom_data_samples = []
    inspection_errors = []
    for actor in actors:
        try:
            actor_components = actor.get_components_by_class(component_class)
        except Exception:
            continue
        for component in actor_components:
            mesh = prop(component, "static_mesh", default=None)
            mesh_path = path_of(mesh) or ""
            if "/OpenMassCrowd/CitySampleVAT/" not in mesh_path:
                continue
            match = re.search(r"/CitySampleVAT/([^/]+)/", mesh_path)
            if match:
                variant_ids.add(match.group(1))
            count = optional_call(component, "get_instance_count")
            count = int(count) if isinstance(count, (int, float)) else 0
            custom_float_count = int(prop(
                component, "num_custom_data_floats", default=0
            ) or 0)
            raw_custom_data = prop(
                component, "per_instance_sm_custom_data", default=[]
            )
            try:
                raw_custom_data = [float(value) for value in raw_custom_data]
            except Exception:
                raw_custom_data = []
            record = {
                "component": path_of(component),
                "owner": path_of(actor),
                "mesh": mesh_path,
                "instance_count": count,
                "num_custom_data_floats": custom_float_count,
                "custom_data_value_count": len(raw_custom_data),
            }
            if custom_float_count > 0 and raw_custom_data:
                record["first_instance_custom_data"] = raw_custom_data[
                    :custom_float_count
                ]
                if len(custom_data_samples) < 12:
                    custom_data_samples.append({
                        "mesh": mesh_path,
                        "values": raw_custom_data[:custom_float_count],
                    })
            components.append(record)
            for instance_index in range(count):
                try:
                    location = transform_translation(
                        component.get_instance_transform(instance_index, True)
                    )
                    if location is None:
                        continue
                    key = (
                        round(float(location.x), 1),
                        round(float(location.y), 1),
                        round(float(location.z), 1),
                    )
                    unique_positions.setdefault(key, location)
                    if direct_target is None:
                        direct_target = location
                except Exception as error:
                    if len(inspection_errors) < 8:
                        inspection_errors.append(
                            "{}: {}".format(type(error).__name__, error)
                        )
    payload = {
        "component_count": len(components),
        "part_instance_count": sum(item["instance_count"] for item in components),
        "components": components,
        "variant_ids": sorted(variant_ids),
        "variant_count": len(variant_ids),
        "custom_data_samples": custom_data_samples,
        "components_with_four_or_more_custom_floats": sum(
            item["num_custom_data_floats"] >= 4 for item in components
        ),
        "direct_target_available": direct_target is not None,
        "unique_instance_transform_count": len(unique_positions),
        "instance_transform_samples": [
            vector_json(location)
            for _key, location in sorted(unique_positions.items())[:12]
        ],
        "entity_count_not_inferred_from_parts": True,
        "identity_note": (
            "Each VAT person is modular; component instances are part instances, "
            "so runtime.metrics.vat remains the authoritative entity count."
        ),
    }
    if inspection_errors:
        payload["inspection_errors"] = inspection_errors[:8]
    return payload, direct_target, [
        location for _key, location in sorted(unique_positions.items())
    ]


def signal_camera(signal_rays):
    locations = [actor.get_actor_location() for actor in signal_rays]
    if not locations:
        raise RuntimeError("signal-regression found no SIG_Ray_* actors")
    minimum = unreal.Vector(
        min(float(item.x) for item in locations),
        min(float(item.y) for item in locations),
        min(float(item.z) for item in locations),
    )
    maximum = unreal.Vector(
        max(float(item.x) for item in locations),
        max(float(item.y) for item in locations),
        max(float(item.z) for item in locations),
    )
    center = unreal.Vector(
        (float(minimum.x) + float(maximum.x)) * 0.5,
        (float(minimum.y) + float(maximum.y)) * 0.5,
        (float(minimum.z) + float(maximum.z)) * 0.5,
    )
    extent = max(
        (float(maximum.x) - float(minimum.x)) * 0.5,
        (float(maximum.y) - float(minimum.y)) * 0.5,
        8000.0,
    )
    target = center
    location = center + unreal.Vector(-1.10 * extent, -1.25 * extent, 0.72 * extent)
    return location, target, minimum, maximum


def camera_plan(world, spawner, preset, context):
    if preset is None:
        return None
    asset = prop(spawner, "central_network_asset")
    bounds = context["bounds"]
    center = bounds["center"]
    extent = bounds["extent"]
    proxies = context["proxies"]
    lanes = context["lanes"]
    plan = {
        "strategy": preset,
        "center_source": "central_network_bounds",
        "visible_proxy_count": len(proxies),
        "visual_target": {},
    }

    if preset == "wide":
        scale = max(8000.0, min(extent, 30000.0))
        target = center + unreal.Vector(0.0, 0.0, 100.0)
        location = center + unreal.Vector(-1.10 * scale, -1.25 * scale, 0.72 * scale)
        plan["visual_target"] = {
            "kind": "complete_certified_network_bounds",
            "represented_entity_count": context["snapshot"]["metrics"]["represented"],
        }
    elif preset == "street":
        anchor, neighbors = dense_proxy_anchor(proxies, center)
        if anchor is None:
            raise RuntimeError("street preset requires at least one visible City Sample proxy")
        cluster = average_location(neighbors)
        lane = nearest_lane(lanes, cluster)
        direction = normalized_xy(lane["points"][0], lane["points"][-1]) if lane else unreal.Vector(1.0, 0.0, 0.0)
        side = unreal.Vector(-float(direction.y), float(direction.x), 0.0)
        target = cluster + unreal.Vector(0.0, 0.0, 90.0)
        location = cluster - direction * 950.0 + side * 750.0 + unreal.Vector(0.0, 0.0, 250.0)
        plan["center_source"] = "densest_visible_proxy_cluster"
        plan["visual_target"] = {
            "kind": "grounding_cluster",
            "anchor_actor": anchor["path"],
            "anchor_tier": anchor["tier"],
            "nearby_visible_proxy_count": len(neighbors),
            "nearest_certified_lane": lane["lane_id"] if lane else None,
            "nearest_lane_class": lane["pedestrian_class"] if lane else None,
        }
    elif preset == "route-overlay":
        scale = max(8000.0, min(extent, 30000.0))
        target = center + unreal.Vector(0.0, 0.0, 80.0)
        location = center + unreal.Vector(-0.55 * scale, -0.85 * scale, 1.15 * scale)
        plan["visual_target"] = {
            "kind": "transient_certified_semantic_lane_overlay",
            "physical_lane_count": len(lanes),
        }
    elif preset == "crossing":
        crossings = [lane for lane in lanes if lane["pedestrian_class"] == "crossing"]
        if not crossings:
            raise RuntimeError("crossing preset found no certified crossing lane")
        vat_payload, _vat_target, vat_positions = collect_vat_instances(
            context["actors"]
        )
        scored = []
        for lane in crossings:
            middle = lane_midpoint(lane)
            nearby_proxies = sum(
                xy_distance_squared(item["location"], middle) <= 2500.0 * 2500.0
                for item in proxies
            )
            nearby_vat = sum(
                xy_distance_squared(location, middle) <= 2500.0 * 2500.0
                for location in vat_positions
            )
            scored.append((
                -(nearby_proxies + nearby_vat),
                -nearby_proxies,
                -nearby_vat,
                -lane["length_cm"],
                lane["lane_id"],
                lane,
            ))
        scored.sort(key=lambda item: item[:5])
        lane = scored[0][5]
        middle = lane_midpoint(lane)
        direction = normalized_xy(lane["points"][0], lane["points"][-1])
        side = unreal.Vector(-float(direction.y), float(direction.x), 0.0)
        target = middle + unreal.Vector(0.0, 0.0, 90.0)
        location = middle - direction * 450.0 + side * 900.0 + unreal.Vector(0.0, 0.0, 300.0)
        plan["center_source"] = "certified_crossing_lane"
        plan["visual_target"] = {
            "kind": "certified_crossing",
            "lane_id": lane["lane_id"],
            "cell_id": lane["cell_id"],
            "pedestrian_class": lane["pedestrian_class"],
            "length_cm": lane["length_cm"],
            "sample_count": len(lane["points"]),
            "nearby_visible_representation_count": -scored[0][0],
            "nearby_visible_proxy_count": -scored[0][1],
            "nearby_vat_instance_transform_count": -scored[0][2],
            "vat_instance_inspection": vat_payload,
            "selection": "most nearby visible proxies, then longest certified crossing",
        }
        plan["_overlay_lane"] = lane
    elif preset in ("lod", "lod-high", "lod-low"):
        requested_tier = {
            "lod-high": "High",
            "lod-low": "Low",
        }.get(preset)
        identity = central_lod_identity_snapshot(spawner)
        actor = stable_snapshot_actor(context["actors"], identity)
        observed_tier = representation_tier(actor) if actor is not None else None
        required_tier = requested_tier or observed_tier
        if actor is None or observed_tier != required_tier:
            raise RuntimeError(
                "{} stable entity is {}, expected {} actor".format(
                    preset, identity.get("representation"), required_tier
                )
            )
        entity_target = snapshot_location(identity)
        # Central photogrammetry contains visually opaque foliage without query
        # collision.  A steep, still-in-band view avoids that false-clear LOS:
        # High stays below 12 m; Low stays between 12 m and 35 m.
        distance = 0.0
        height = 800.0 if required_tier == "High" else 2000.0
        pawn = unreal.GameplayStatics.get_player_pawn(world, 0)
        location, los = clear_lod_camera(
            world, entity_target, distance, height, [actor, spawner, pawn]
        )
        target = entity_target + unreal.Vector(0.0, 0.0, 95.0)
        plan["center_source"] = "stable_mass_entity_{}_actor".format(
            required_tier.lower()
        )
        plan["visual_target"] = {
            "kind": "stable_mass_entity_representation_tier_actor",
            "actor": path_of(actor),
            "tier": observed_tier,
            "required_tier": required_tier,
            "stable_identity": identity,
            "line_of_sight": los,
            "target_marker": None,
        }
        plan["_tier_actor"] = actor
        plan["_tier"] = required_tier
        plan["_lod_identity_pre"] = identity
        plan["_follow_height_cm"] = height
    elif preset == "lod-vat":
        identity = central_lod_identity_snapshot(spawner)
        if identity.get("representation") != "VAT":
            raise RuntimeError(
                "lod-vat stable entity is {}, expected VAT".format(
                    identity.get("representation")
                )
            )
        vat_payload, _first_vat_target, vat_positions = collect_vat_instances(
            context["actors"]
        )
        vat_count = context["snapshot"]["metrics"]["vat"]
        if not isinstance(vat_count, (int, float)) or vat_count <= 0:
            raise RuntimeError("lod-vat preset requires a positive runtime VAT entity count")
        entity_target = snapshot_location(identity)
        nearest_vat = min(
            vat_positions,
            key=lambda point: xy_distance_squared(point, entity_target),
        ) if vat_positions else None
        nearest_distance = (
            math.sqrt(xy_distance_squared(nearest_vat, entity_target))
            if nearest_vat is not None else None
        )
        pawn = unreal.GameplayStatics.get_player_pawn(world, 0)
        location, los = clear_lod_camera(
            world, entity_target, 0.0, 4500.0, [spawner, pawn]
        )
        target = entity_target + unreal.Vector(0.0, 0.0, 95.0)
        plan["center_source"] = "stable_mass_entity_vat_transform"
        plan["visual_target"] = {
            "kind": "stable_mass_entity_city_sample_vat_ism",
            "runtime_vat_entity_count": vat_count,
            "stable_identity": identity,
            "nearest_exposed_vat_transform_distance_cm": nearest_distance,
            "line_of_sight": los,
            "target_marker": None,
        }
        plan["_vat_inspection"] = vat_payload
        plan["_vat_target"] = entity_target
        plan["_lod_identity_pre"] = identity
        plan["_follow_height_cm"] = 4500.0
    elif preset == "telemetry":
        scale = max(8000.0, min(extent * 0.55, 15000.0))
        target = center + unreal.Vector(0.0, 0.0, 100.0)
        location = center + unreal.Vector(-0.85 * scale, -scale, 0.52 * scale)
        plan["visual_target"] = {
            "kind": "telemetry_visual_context",
            "hud_rendered": False,
            "screenshot_role": "context_only",
            "authoritative_data": "report.runtime.metrics and report.telemetry_summary",
            "reason": "The observer does not inject a UMG/stat HUD.",
        }
    elif preset == "signal-regression":
        signal = context["signal_inventory"]
        if not signal["strict_visible_regression_pass"]:
            raise RuntimeError(
                "signal-regression inventory mismatch: {}".format(
                    json.dumps(signal, ensure_ascii=False, sort_keys=True)
                )
            )
        _signal_location, _signal_target, minimum, maximum = signal_camera(
            context["signal_rays"]
        )
        # Frame the Central crowd core, not the complete 1.35 km ray footprint,
        # so people and the unmodified four-color signal scene remain co-visible.
        scale = max(8000.0, min(extent, 30000.0))
        target = center + unreal.Vector(0.0, 0.0, 100.0)
        location = center + unreal.Vector(-1.10 * scale, -1.25 * scale, 0.72 * scale)
        plan["center_source"] = "central_network_bounds_with_unhidden_signal_scene"
        plan["visual_target"] = {
            "kind": "central_crowd_and_unhidden_telecom_signal_regression",
            "signal_bounds_min": vector_json(minimum),
            "signal_bounds_max": vector_json(maximum),
            "strict_visible_inventory_pass": True,
            "framing_note": (
                "Central core is framed for crowd/ray co-visibility; complete ray "
                "inventory is proven by JSON rather than fitting every ray in frame."
            ),
        }
    else:
        raise RuntimeError("unsupported camera preset: {}".format(preset))

    plan.update({
        "location": location,
        "rotation": unreal.MathLibrary.find_look_at_rotation(location, target),
        "target": target,
    })
    return plan


def build_capture_context(world, spawner, snapshot):
    actors = list(unreal.GameplayStatics.get_all_actors_of_class(world, unreal.Actor))
    signal, signal_visuals, signal_rays = signal_inventory(world, actors)
    asset = prop(spawner, "central_network_asset")
    return {
        "actors": actors,
        "snapshot": snapshot,
        "bounds": bounds_info(asset),
        "lanes": certified_lanes(asset, physical_once=True),
        "proxies": collect_visible_proxies(world),
        "signal_inventory": signal,
        "signal_visuals": signal_visuals,
        "signal_rays": signal_rays,
    }


def hide_signal_visuals(context, report):
    records = []
    report["signal_visual_isolation"] = {
        "requested": True,
        "matched_actor_count": len(context["signal_visuals"]),
        "changed_actor_count": 0,
        "already_hidden_actor_count": 0,
        "selection": "PIE SIG_Ray_* and SIG_Source_* actors",
        "map_or_asset_modified": False,
        "restoration_attempted": False,
        "restoration_passed": False,
    }
    try:
        for actor in context["signal_visuals"]:
            was_hidden = actor_hidden(actor)
            record = {
                "actor": actor,
                "path": path_of(actor),
                "label": actor_label(actor),
                "was_hidden": was_hidden,
                "changed": False,
            }
            records.append(record)
            if not was_hidden:
                actor.set_actor_hidden_in_game(True)
                if not actor_hidden(actor):
                    raise RuntimeError(
                        "failed to temporarily hide signal visual {}".format(
                            record["label"]
                        )
                    )
                record["changed"] = True
        report["signal_visual_isolation"].update({
            "changed_actor_count": sum(item["changed"] for item in records),
            "already_hidden_actor_count": sum(item["was_hidden"] for item in records),
        })
    except Exception:
        restore_signal_visuals(records, report)
        raise
    return records


def restore_signal_visuals(records, report):
    isolation = report.get("signal_visual_isolation")
    if isolation is None:
        return True
    if not isolation.get("requested"):
        return True
    isolation["restoration_attempted"] = True
    restored = 0
    errors = []
    for record in records:
        actor = record["actor"]
        try:
            actor.set_actor_hidden_in_game(record["was_hidden"])
            if actor_hidden(actor) != record["was_hidden"]:
                raise RuntimeError("hidden state did not round-trip")
            restored += 1
        except Exception as error:
            errors.append({
                "actor": record["path"],
                "error": "{}: {}".format(type(error).__name__, error),
            })
    isolation["restored_actor_count"] = restored
    isolation["restoration_errors"] = errors
    isolation["restoration_passed"] = restored == len(records) and not errors
    return isolation["restoration_passed"]


def evenly_spaced_indices(total, limit):
    if total <= 0 or limit <= 0:
        return []
    if total <= limit:
        return list(range(total))
    if limit == 1:
        return [0]
    return sorted({
        int(round(index * float(total - 1) / float(limit - 1)))
        for index in range(limit)
    })


def overlay_color(class_key):
    colors = {
        "sidewalk": unreal.LinearColor(0.0, 0.85, 1.0, 1.0),
        "footway": unreal.LinearColor(0.05, 1.0, 0.25, 1.0),
        "crossing": unreal.LinearColor(1.0, 0.05, 0.75, 1.0),
        "pedestrianzone": unreal.LinearColor(1.0, 0.82, 0.0, 1.0),
        "manual_link": unreal.LinearColor(0.60, 0.20, 1.0, 1.0),
        "unknown": unreal.LinearColor(1.0, 1.0, 1.0, 1.0),
    }
    return colors.get(class_key, colors["unknown"])


def offset_point(value, z_offset=18.0):
    return unreal.Vector(float(value.x), float(value.y), float(value.z) + z_offset)


def draw_route_overlay(world, lanes):
    segments = []
    class_lane_counts = {}
    total_sample_segments = 0
    for lane in lanes:
        class_key = lane["pedestrian_class"]
        class_lane_counts[class_key] = class_lane_counts.get(class_key, 0) + 1
        points = lane["points"]
        total_sample_segments += len(points) - 1
        for index in range(len(points) - 1):
            segments.append((lane, points[index], points[index + 1]))
    selected_indices = evenly_spaced_indices(
        len(segments), MAX_ROUTE_OVERLAY_SEGMENTS
    )
    drawn_by_class = {}
    for index in selected_indices:
        lane, start, end = segments[index]
        class_key = lane["pedestrian_class"]
        drawn_by_class[class_key] = drawn_by_class.get(class_key, 0) + 1
        unreal.SystemLibrary.draw_debug_line(
            world,
            offset_point(start),
            offset_point(end),
            overlay_color(class_key),
            OVERLAY_DURATION_SECONDS,
            5.0 if class_key == "crossing" else 2.5,
        )
    return {
        "kind": "certified_network_semantic_routes",
        "directional_reverse_pairs_drawn_once": True,
        "physical_lane_count": len(lanes),
        "class_lane_counts": class_lane_counts,
        "total_certified_sample_segments": total_sample_segments,
        "drawn_segment_count": len(selected_indices),
        "drawn_segments_by_class": drawn_by_class,
        "maximum_draw_budget": MAX_ROUTE_OVERLAY_SEGMENTS,
        "sampling": "deterministic evenly spaced indices over sorted physical lanes",
        "z_offset_cm": 18.0,
        "duration_seconds": OVERLAY_DURATION_SECONDS,
        "persistent": False,
        "legend": {
            "sidewalk": "cyan",
            "footway": "green",
            "crossing": "magenta",
            "pedestrian_zone": "yellow",
            "manual_link": "purple",
            "unknown": "white",
        },
    }


def draw_crossing_overlay(world, lane):
    points = lane["points"]
    for index in range(len(points) - 1):
        unreal.SystemLibrary.draw_debug_line(
            world,
            offset_point(points[index], 22.0),
            offset_point(points[index + 1], 22.0),
            overlay_color("crossing"),
            OVERLAY_DURATION_SECONDS,
            9.0,
        )
    for point in (points[0], points[-1]):
        unreal.SystemLibrary.draw_debug_sphere(
            world,
            offset_point(point, 35.0),
            65.0,
            12,
            overlay_color("crossing"),
            OVERLAY_DURATION_SECONDS,
            4.0,
        )
    return {
        "kind": "selected_certified_crossing",
        "lane_id": lane["lane_id"],
        "cell_id": lane["cell_id"],
        "pedestrian_class": lane["pedestrian_class"],
        "drawn_segment_count": len(points) - 1,
        "endpoint_marker_count": 2,
        "duration_seconds": OVERLAY_DURATION_SECONDS,
        "persistent": False,
        "color": "magenta",
    }


def draw_tier_marker(world, location, tier):
    colors = {
        "High": unreal.LinearColor(0.0, 0.55, 1.0, 1.0),
        "Low": unreal.LinearColor(1.0, 0.82, 0.0, 1.0),
        "VAT": unreal.LinearColor(0.75, 0.15, 1.0, 1.0),
    }
    color = colors.get(tier, unreal.LinearColor(1.0, 1.0, 1.0, 1.0))
    center = offset_point(location, 95.0)
    unreal.SystemLibrary.draw_debug_sphere(
        world, center, 95.0, 16, color, OVERLAY_DURATION_SECONDS, 4.0
    )
    unreal.SystemLibrary.draw_debug_line(
        world,
        offset_point(location, 0.0),
        offset_point(location, 260.0),
        color,
        OVERLAY_DURATION_SECONDS,
        4.0,
    )
    return {
        "kind": "representation_tier_target",
        "tier": tier,
        "marker_center": vector_json(center),
        "duration_seconds": OVERLAY_DURATION_SECONDS,
        "persistent": False,
        "color": {"High": "blue", "Low": "yellow", "VAT": "purple"}.get(
            tier, "white"
        ),
    }


def apply_transient_overlay(state):
    preset = state["preset"]
    plan = state.get("plan") or {}
    world, _spawner = runtime_objects()
    overlay = None
    if preset == "route-overlay":
        overlay = draw_route_overlay(world, state["context"]["lanes"])
    elif preset == "crossing":
        overlay = draw_crossing_overlay(world, plan["_overlay_lane"])
    elif preset in ("lod", "lod-high", "lod-low", "lod-vat"):
        overlay = None
    if overlay is not None:
        state["report"]["transient_visual_overlay"] = overlay
        state["overlay_duration"] = OVERLAY_DURATION_SECONDS
    else:
        state["report"]["transient_visual_overlay"] = {
            "kind": "none",
            "reason": "preset uses unmodified scene visuals",
        }
        state["overlay_duration"] = 0.0


def telemetry_summary(snapshot):
    metrics = snapshot["metrics"]
    keys = (
        "admission_target",
        "admitted",
        "minimum_planned_spawn_clearance_cm",
        "simulated",
        "represented",
        "high_actor",
        "low_actor",
        "vat",
        "expected_moving",
        "moving",
        "stuck",
        "unsupported_visuals",
        "severe_overlap_pairs",
        "severe_overlap_agents",
        "peak_severe_overlap_pairs",
        "peak_severe_overlap_agents",
        "severe_overlap_pair_observations",
        "minimum_center_distance_cm",
        "minimum_observed_center_distance_cm",
        "frame_p50_ms",
        "frame_p95_ms",
        "frame_maximum_ms",
        "frame_sample_count",
        "frame_window_seconds",
        "ground_guard_queries",
        "ground_candidate_component_tests",
        "ground_component_cache_refreshes",
    )
    return {
        "hud_rendered": False,
        "screenshot_role": "visual context only; counts are not burned into pixels",
        "authoritative_source": "reflected AOpenMassCrowdSpawner getters in this JSON",
        "values": {key: metrics.get(key) for key in keys},
        "missing_fields": [key for key in keys if metrics.get(key) is None],
    }


def finite_at_least(value, minimum):
    return bool(
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value >= minimum
    )


def collision_contract(snapshot):
    metrics = snapshot["metrics"]
    values = {
        "minimum_planned_spawn_clearance_cm": metrics.get(
            "minimum_planned_spawn_clearance_cm"
        ),
        "current_minimum_center_distance_cm": metrics.get(
            "minimum_center_distance_cm"
        ),
        "minimum_observed_center_distance_cm": metrics.get(
            "minimum_observed_center_distance_cm"
        ),
        "current_overlap_pairs": metrics.get("severe_overlap_pairs"),
        "current_overlap_agents": metrics.get("severe_overlap_agents"),
        "lifetime_peak_overlap_pairs": metrics.get("peak_severe_overlap_pairs"),
        "lifetime_peak_overlap_agents": metrics.get("peak_severe_overlap_agents"),
        "lifetime_overlap_pair_observations": metrics.get(
            "severe_overlap_pair_observations"
        ),
    }
    checks = {
        "planned_spawn_clearance_at_least_hard_20cm": finite_at_least(
            values["minimum_planned_spawn_clearance_cm"],
            SEVERE_OVERLAP_THRESHOLD_CM,
        ),
        "current_center_clearance_at_least_20cm": finite_at_least(
            values["current_minimum_center_distance_cm"],
            SEVERE_OVERLAP_THRESHOLD_CM,
        ),
        "zero_current_overlap_pairs": values["current_overlap_pairs"] == 0,
        "zero_current_overlap_agents": values["current_overlap_agents"] == 0,
    }
    return {
        "required_gate_runtime_report_schema_version": (
            REQUIRED_GATE_RUNTIME_REPORT_SCHEMA_VERSION
        ),
        "required_gate_verifier_check_name": REQUIRED_COLLISION_CHECK_NAME,
        "pedestrian_radius_cm": 27.0,
        "severe_overlap_threshold_cm": SEVERE_OVERLAP_THRESHOLD_CM,
        "planned_spawn_quality_target_cm": (
            PLANNED_CENTER_CLEARANCE_QUALITY_TARGET_CM
        ),
        "planned_spawn_quality_target_is_advisory": True,
        "scope": (
            "This instantaneous capture checks the current hard 20 cm state only; "
            "the named schema-4 gate verifier remains authoritative for the full "
            "60-second steady window."
        ),
        "values": values,
        "checks": checks,
        "hard_snapshot_checks_passed": all(checks.values()),
        "cumulative_snapshot_checks_passed": all(checks.values()),
        "quality_diagnostics": {
            "planned_spawn_clearance_quality_target_met": finite_at_least(
                values["minimum_planned_spawn_clearance_cm"],
                PLANNED_CENTER_CLEARANCE_QUALITY_TARGET_CM,
            ),
            "minimum_observed_center_distance_cm": values[
                "minimum_observed_center_distance_cm"
            ],
            "lifetime_peak_overlap_pairs": values[
                "lifetime_peak_overlap_pairs"
            ],
            "lifetime_peak_overlap_agents": values[
                "lifetime_peak_overlap_agents"
            ],
            "lifetime_overlap_pair_observations": values[
                "lifetime_overlap_pair_observations"
            ],
            "advisory_not_spec_gate": True,
        },
        "schema4_gate_check_evaluated": False,
        "schema4_sampled_peak_requirements": {
            "maximum_sampled_current_pairs": 0,
            "maximum_sampled_current_agents": 0,
            "evaluated_by_this_capture": False,
        },
        "observer_limit": (
            "This instantaneous capture validates cumulative counters but cannot "
            "compute sampled peaks; it does not replace the schema-4 60-second gate "
            "verifier or claim that its named check passed."
        ),
    }


def new_report(label, safe, preset, paths):
    return {
        "schema_id": "open_mass_central_evidence_capture",
        "schema_version": 2,
        "required_gate_runtime_report_schema_version": (
            REQUIRED_GATE_RUNTIME_REPORT_SCHEMA_VERSION
        ),
        "generated_utc": utc_now(),
        "status": "IN_PROGRESS",
        "capture_passed": False,
        "acceptance_gate_evaluated": False,
        "label": label,
        "safe_label": safe,
        "camera_preset": preset or "current",
        "timestamped_image_path": str(paths["png"].resolve()),
        "label_latest_image_path": str(paths["latest_png"].resolve()),
        "timestamped_report_path": str(paths["json"].resolve()),
        "label_latest_report_path": str(paths["latest_json"].resolve()),
        "safety": {
            "existing_pie_only": True,
            "starts_or_stops_pie": False,
            "mutates_map_spawner_network_population_or_entities": False,
            "camera_is_editor_viewport_only": True,
            "temporary_signal_actor_visibility_may_change": (
                preset in SIGNAL_HIDDEN_PRESETS
            ),
            "temporary_signal_visibility_must_restore_before_pass": True,
            "debug_overlays_are_transient_and_nonpersistent": True,
        },
    }


def write_reports(paths, report):
    report["generated_utc"] = utc_now()
    atomic_json(paths["json"], report)
    atomic_json(paths["latest_json"], report)


def unregister(state):
    handle = state.get("handle")
    if handle is not None:
        try:
            unreal.unregister_slate_post_tick_callback(handle)
        except Exception:
            pass
    if getattr(builtins, CALLBACK_KEY, None) == handle:
        delattr(builtins, CALLBACK_KEY)
    if getattr(builtins, STATE_KEY, None) is state:
        delattr(builtins, STATE_KEY)


def evaluate_visual_evidence(state, world, snapshot):
    preset = state["preset"]
    metrics = snapshot["metrics"]
    checks = {}
    notes = []
    if preset != "signal-regression":
        checks["positive_simulated_population"] = isinstance(
            metrics.get("simulated"), (int, float)
        ) and metrics["simulated"] > 0
        checks["all_simulated_entities_represented"] = (
            metrics.get("represented") == metrics.get("simulated")
            and checks["positive_simulated_population"]
        )
        checks["zero_unsupported_visuals"] = metrics.get("unsupported_visuals") == 0
        contract = collision_contract(snapshot)
        state["report"]["collision_contract"] = contract
        checks["schema4_collision_current_snapshot_preconditions"] = contract[
            "hard_snapshot_checks_passed"
        ]

    overlay = state["report"].get("transient_visual_overlay", {})
    plan = state.get("plan") or {}
    if preset == "street":
        checks["grounding_cluster_is_visible"] = (
            plan.get("visual_target", {}).get("nearby_visible_proxy_count", 0) > 0
        )
    elif preset == "route-overlay":
        checks["certified_route_segments_drawn"] = overlay.get(
            "drawn_segment_count", 0
        ) > 0
        checks["crossing_semantics_present_in_overlay"] = overlay.get(
            "drawn_segments_by_class", {}
        ).get("crossing", 0) > 0
    elif preset == "crossing":
        target = plan.get("visual_target", {})
        checks["target_is_certified_crossing"] = (
            target.get("pedestrian_class") == "crossing"
            and overlay.get("drawn_segment_count", 0) > 0
        )
        checks["pedestrian_visible_near_crossing"] = (
            target.get("nearby_visible_representation_count", 0) > 0
        )
        if not checks["pedestrian_visible_near_crossing"]:
            notes.append(
                "Retry crossing capture later; no actor proxy was within 25 m at preflight."
            )
    elif preset in ("lod", "lod-high", "lod-low"):
        tier = plan.get("_tier")
        _runtime_world, spawner = runtime_objects()
        pre_identity = plan.get("_lod_identity_pre", {})
        post_identity = central_lod_identity_snapshot(spawner)
        observed_tier = post_identity.get("actor_tier", "Unavailable")
        target_visible = (
            post_identity.get("representation") == tier + "Actor"
            and not post_identity.get("actor_hidden", True)
        )
        identity_fields = (
            "stable_entity_slot",
            "mass_entity_index",
            "mass_entity_serial",
            "appearance_seed",
            "variant_index",
            "variant_name",
        )
        identity_stable = all(
            pre_identity.get(field) == post_identity.get(field)
            for field in identity_fields
        )
        state["report"].setdefault("lod_target_validation", {}).update({
            "required_tier": tier,
            "post_capture_observed_tier": observed_tier,
            "post_capture_target_visible": target_visible,
            "stable_identity_fields": list(identity_fields),
            "stable_identity_preserved": identity_stable,
            "pre_capture": pre_identity,
            "post_capture": post_identity,
        })
        checks["tier_target_survived_capture"] = target_visible
        checks["stable_mass_identity_preserved"] = identity_stable
        checks["actor_appearance_seed_matches_mass_identity"] = (
            post_identity.get("actor_appearance_seed")
            == post_identity.get("appearance_seed")
        )
        actor_claim = "{} {}".format(
            post_identity.get("actor_path", ""),
            post_identity.get("actor_class", ""),
        ).lower()
        checks["no_placeholder_actor"] = (
            "mannequin" not in actor_claim
            and "baseproxy" not in actor_claim
            and "openmasscrowdcitysample" in actor_claim
        )
        if preset != "lod":
            checks["target_tier_matches_preset"] = observed_tier == tier
            metric_key = "high_actor" if tier == "High" else "low_actor"
            checks["runtime_tier_count_positive"] = isinstance(
                metrics.get(metric_key), (int, float)
            ) and metrics[metric_key] > 0
    elif preset == "lod-vat":
        _runtime_world, spawner = runtime_objects()
        pre_identity = plan.get("_lod_identity_pre", {})
        post_identity = central_lod_identity_snapshot(spawner)
        identity_fields = (
            "stable_entity_slot",
            "mass_entity_index",
            "mass_entity_serial",
            "appearance_seed",
            "variant_index",
            "variant_name",
        )
        identity_stable = all(
            pre_identity.get(field) == post_identity.get(field)
            for field in identity_fields
        )
        frame_count = max(
            1.0,
            float(pre_identity.get("vat_end_frame", 0.0))
            - float(pre_identity.get("vat_start_frame", 0.0))
            + 1.0,
        )
        raw_frame_delta = abs(
            float(post_identity.get("vat_current_frame", 0.0))
            - float(pre_identity.get("vat_current_frame", 0.0))
        )
        frame_delta = min(raw_frame_delta, frame_count - raw_frame_delta)
        state["report"]["lod_target_validation"] = {
            "required_tier": "VAT",
            "stable_identity_fields": list(identity_fields),
            "stable_identity_preserved": identity_stable,
            "vat_frame_delta": frame_delta,
            "pre_capture": pre_identity,
            "post_capture": post_identity,
        }
        checks["runtime_vat_entity_count_positive"] = isinstance(
            metrics.get("vat"), (int, float)
        ) and metrics["vat"] > 0
        post_vat, _post_target, _post_positions = collect_vat_instances(list(
            unreal.GameplayStatics.get_all_actors_of_class(world, unreal.Actor)
        ))
        state["report"]["vat_instance_inspection_post_capture"] = post_vat
        checks["stable_mass_identity_preserved"] = identity_stable
        checks["stable_entity_remained_vat"] = (
            post_identity.get("representation") == "VAT"
        )
        checks["vat_playback_frame_advanced"] = frame_delta > 0.5
        checks["six_official_vat_appearance_variants_present"] = (
            post_vat.get("variant_count", 0) >= 6
        )
        checks["vat_custom_data_uploaded_to_every_component"] = (
            post_vat.get("component_count", 0) > 0
            and post_vat.get("components_with_four_or_more_custom_floats", 0)
            == post_vat.get("component_count", 0)
        )
        vat_paths = " ".join(
            item.get("mesh", "") for item in post_vat.get("components", [])
        ).lower()
        checks["no_placeholder_vat_mesh"] = (
            "mannequin" not in vat_paths
            and "baseproxy" not in vat_paths
            and "/openmasscrowd/citysamplevat/" in vat_paths
        )
    elif preset == "telemetry":
        summary = telemetry_summary(snapshot)
        state["report"]["telemetry_summary"] = summary
        checks["critical_telemetry_fields_present"] = not summary["missing_fields"]
        notes.append(
            "No HUD was injected. The PNG is context only; use telemetry_summary.values."
        )
    elif preset == "signal-regression":
        signal, _visuals, _rays = signal_inventory(world)
        state["report"]["signal_inventory_post_capture"] = signal
        checks["strict_visible_signal_inventory"] = signal[
            "strict_visible_regression_pass"
        ]

    result = {
        "all_required_checks_passed": all(checks.values()) if checks else True,
        "checks": checks,
        "notes": notes,
        "does_not_replace_gate_verifier": True,
        "acceptance_gate_evaluated": False,
    }
    state["report"]["visual_evidence_checks"] = result
    return result["all_required_checks_passed"]


def finalize_suite(suite, passed, reason, error=None):
    if suite.get("finished"):
        return
    suite["finished"] = True
    payload = {
        "schema_id": "open_mass_central_evidence_suite",
        "schema_version": 1,
        "required_gate_runtime_report_schema_version": (
            REQUIRED_GATE_RUNTIME_REPORT_SCHEMA_VERSION
        ),
        "generated_utc": utc_now(),
        "status": "PASS" if passed else "FAIL",
        "suite_passed": passed,
        "completion_reason": reason,
        "suite": suite["name"],
        "label": suite["label"],
        "safe_label": suite["safe_label"],
        "requested_presets": suite["requested_presets"],
        "completed_capture_count": len(suite["items"]),
        "expected_capture_count": len(suite["requested_presets"]),
        "captures": suite["items"],
        "timestamped_report_path": str(suite["paths"]["json"].resolve()),
        "label_latest_report_path": str(
            suite["paths"]["latest_json"].resolve()
        ),
        "acceptance_gate_evaluated": False,
        "does_not_start_or_stop_pie": True,
        "telemetry_hud_injected": False,
        "collision_contract": {
            "required_gate_verifier_check_name": REQUIRED_COLLISION_CHECK_NAME,
            "severe_overlap_threshold_cm": SEVERE_OVERLAP_THRESHOLD_CM,
            "planned_spawn_quality_target_cm": (
                PLANNED_CENTER_CLEARANCE_QUALITY_TARGET_CM
            ),
            "planned_spawn_quality_target_is_advisory": True,
            "requires_zero_current_overlap_violations": True,
            "steady_window_schema4_gate_is_authoritative": True,
            "sampled_peaks_must_be_zero_in_schema4_report": True,
        },
    }
    if error is not None:
        payload["error"] = "{}: {}".format(type(error).__name__, error)
    atomic_json(suite["paths"]["json"], payload)
    atomic_json(suite["paths"]["latest_json"], payload)
    marker = {
        "status": payload["status"],
        "suite": suite["name"],
        "label": suite["safe_label"],
        "capture_count": len(suite["items"]),
        "report": payload["timestamped_report_path"],
        "latest_report": payload["label_latest_report_path"],
    }
    message = "OPEN_MASS_CENTRAL_EVIDENCE_SUITE=" + json.dumps(
        marker, ensure_ascii=False, sort_keys=True
    )
    (unreal.log_warning if passed else unreal.log_error)(message)


def advance_suite(state, passed):
    suite = state.get("suite")
    if suite is None:
        return
    report = state["report"]
    suite["items"].append({
        "preset": state["preset"],
        "label": report["safe_label"],
        "status": report["status"],
        "capture_passed": report["capture_passed"],
        "image": report["timestamped_image_path"],
        "latest_image": report["label_latest_image_path"],
        "report": report["timestamped_report_path"],
        "latest_report": report["label_latest_report_path"],
    })
    if not passed:
        finalize_suite(suite, False, "capture_failed")
        return
    if not suite["remaining"]:
        finalize_suite(suite, True, "all_captures_passed")
        return
    preset = suite["remaining"].pop(0)
    child_label = "{}-{}".format(suite["safe_label"], preset)
    delay = state.get("overlay_duration", 0.0) + OVERLAY_CLEARANCE_SECONDS
    try:
        start(child_label, preset, suite_state=suite, initial_delay=delay)
    except Exception as error:
        suite["items"].append({
            "preset": preset,
            "label": child_label,
            "status": "FAIL",
            "capture_passed": False,
            "error": "{}: {}".format(type(error).__name__, error),
        })
        finalize_suite(suite, False, "next_capture_start_failed", error)


def finish(state, passed, reason, error=None):
    if state.get("finished"):
        return
    state["finished"] = True
    report = state["report"]
    if (state.get("plan") or {}).get("_follow_height_cm") is not None:
        report["lod_camera_follow"] = {
            "enabled": True,
            "update_count": state.get("follow_updates", 0),
            "height_cm": (state.get("plan") or {}).get("_follow_height_cm"),
            "last_target": state.get("follow_last_target"),
            "last_camera_location": state.get("follow_last_location"),
            "moves_mass_entity": False,
        }

    world = None
    snapshot = None
    try:
        world, spawner = runtime_objects()
        if path_of(world) != state["world"] or path_of(spawner) != state["spawner"]:
            raise RuntimeError("PIE world or Central spawner changed during capture")
        snapshot = runtime_snapshot(world, spawner)
        report["runtime"] = snapshot
    except Exception as context_error:
        passed = False
        reason = "runtime_context_changed"
        error = error or context_error

    if not restore_signal_visuals(state.get("signal_visibility_records", []), report):
        passed = False
        reason = "signal_visibility_restore_failed"

    if state.get("camera_applied"):
        try:
            subsystem = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem)
            original = state["original_camera"]
            subsystem.set_level_viewport_camera_info(original[0], original[1])
        except Exception as restore_error:
            passed = False
            reason = "camera_restore_failed"
            error = error or restore_error

    if world is not None and snapshot is not None:
        try:
            report["telemetry_summary"] = telemetry_summary(snapshot)
            report["collision_contract"] = collision_contract(snapshot)
            report["signal_inventory_after_restore"] = signal_inventory(world)[0]
            if not evaluate_visual_evidence(state, world, snapshot):
                passed = False
                reason = "visual_evidence_preconditions_failed"
        except Exception as validation_error:
            passed = False
            reason = "visual_evidence_validation_failed"
            error = error or validation_error

    unregister(state)
    report["status"] = "PASS" if passed else "FAIL"
    report["capture_passed"] = passed
    report["completion_reason"] = reason
    if error is not None:
        report["error"] = "{}: {}".format(type(error).__name__, error)
    try:
        write_reports(state["paths"], report)
    except Exception as report_error:
        unreal.log_error("OPEN_MASS_CENTRAL_EVIDENCE_REPORT_ERROR={}".format(report_error))
        return

    marker = {
        "status": report["status"],
        "label": report["safe_label"],
        "image": report["timestamped_image_path"],
        "latest_image": report["label_latest_image_path"],
        "report": report["timestamped_report_path"],
        "latest_report": report["label_latest_report_path"],
    }
    message = "OPEN_MASS_CENTRAL_EVIDENCE=" + json.dumps(
        marker, ensure_ascii=False, sort_keys=True
    )
    (unreal.log_warning if passed else unreal.log_error)(message)
    advance_suite(state, passed)


def validate_identity(state):
    world, spawner = runtime_objects()
    if path_of(world) != state["world"] or path_of(spawner) != state["spawner"]:
        raise RuntimeError("PIE world or Central spawner changed during capture")


def follow_lod_target(state, now):
    """Keep the gameplay and editor cameras centered on one moving Mass entity."""
    plan = state.get("plan") or {}
    height = plan.get("_follow_height_cm")
    if height is None or now < state.get("follow_next", 0.0):
        return
    world, spawner = runtime_objects()
    identity = central_lod_identity_snapshot(spawner)
    pre_identity = plan.get("_lod_identity_pre", {})
    identity_fields = (
        "stable_entity_slot",
        "mass_entity_index",
        "mass_entity_serial",
        "appearance_seed",
        "variant_index",
        "variant_name",
    )
    if not all(pre_identity.get(field) == identity.get(field) for field in identity_fields):
        raise RuntimeError("stable Central LOD identity changed while following camera")

    target = snapshot_location(identity)
    location = target + unreal.Vector(0.0, 0.0, float(height))
    look_target = target + unreal.Vector(0.0, 0.0, 95.0)
    rotation = unreal.MathLibrary.find_look_at_rotation(location, look_target)
    controller = unreal.GameplayStatics.get_player_controller(world, 0)
    pawn = unreal.GameplayStatics.get_player_pawn(world, 0)
    if controller is None or pawn is None:
        raise RuntimeError("PIE viewer is unavailable while following LOD target")
    pawn.set_actor_location(location, False, True)
    pawn.set_actor_rotation(rotation, True)
    controller.set_control_rotation(rotation)
    subsystem = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem)
    subsystem.set_level_viewport_camera_info(location, rotation)

    state["follow_next"] = now + 0.1
    state["follow_updates"] = state.get("follow_updates", 0) + 1
    state["follow_last_target"] = vector_json(target)
    state["follow_last_location"] = vector_json(location)


def tick(_delta_seconds):
    state = getattr(builtins, STATE_KEY, None)
    if state is None or state.get("finished"):
        return
    now = time.monotonic()
    try:
        follow_lod_target(state, now)
    except Exception as error:
        finish(state, False, "lod_camera_follow_failed", error)
        return
    if now < state["next"]:
        return
    if now >= state["deadline"]:
        finish(state, False, "screenshot_file_timeout")
        return
    try:
        validate_identity(state)
        if state["stage"] == "prepare_visuals":
            apply_transient_overlay(state)
            state["stage"] = "settle"
            state["next"] = now + SETTLE_SECONDS
            return
        if state["stage"] == "settle":
            state["stage"] = "wait_file"  # Advance before re-entrant Slate work.
            state["next"] = now + 0.1
            state["report"]["screenshot_request_return"] = json_value(
                unreal.AutomationLibrary.take_high_res_screenshot(
                    WIDTH,
                    HEIGHT,
                    str(state["paths"]["png"]),
                    camera=None,
                    mask_enabled=False,
                    capture_hdr=False,
                    delay=0.0,
                    force_game_view=True,
                )
            )
            return

        image = state["paths"]["png"]
        if not image.is_file() or image.stat().st_size <= 0:
            state["next"] = now + 0.1
            return
        size = image.stat().st_size
        if size != state.get("size"):
            state["size"], state["stable_since"] = size, now
            state["next"] = now + 0.1
            return
        if now - state["stable_since"] < 0.25:
            state["next"] = now + 0.1
            return
        width, height = png_dimensions(image)
        if (width, height) != (WIDTH, HEIGHT):
            raise RuntimeError("screenshot is {}x{}, expected {}x{}".format(
                width, height, WIDTH, HEIGHT
            ))
        validate_identity(state)
        atomic_copy(image, state["paths"]["latest_png"])
        if png_dimensions(state["paths"]["latest_png"]) != (WIDTH, HEIGHT):
            raise RuntimeError("label-latest PNG failed validation")
        state["report"]["image"] = {
            "actual_path": str(image.resolve()),
            "actual_label_latest_path": str(state["paths"]["latest_png"].resolve()),
            "bytes": size,
            "width": width,
            "height": height,
            "png_validated": True,
        }
        finish(state, True, "screenshot_written_and_validated")
    except Exception as error:
        finish(state, False, "capture_exception", error)


def start(label, preset, suite_state=None, initial_delay=0.0):
    if getattr(builtins, STATE_KEY, None) is not None:
        raise RuntimeError("another Central evidence capture is still active")
    safe = safe_label(label)
    directory = report_dir()
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    paths = output_paths(directory, safe, stamp)
    report = new_report(label, safe, preset, paths)
    try:
        world, spawner = runtime_objects()
        snapshot = runtime_snapshot(world, spawner)
        context = build_capture_context(world, spawner, snapshot)
        subsystem = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem)
        original_camera = subsystem.get_level_viewport_camera_info()
        plan = camera_plan(world, spawner, preset, context)
    except Exception as error:
        report.update({
            "status": "FAIL",
            "completion_reason": "preflight_failed",
            "error": "{}: {}".format(type(error).__name__, error),
        })
        write_reports(paths, report)
        unreal.log_error("OPEN_MASS_CENTRAL_EVIDENCE_PREFLIGHT_ERROR={}".format(error))
        raise

    report["runtime"] = snapshot
    report["telemetry_summary"] = telemetry_summary(snapshot)
    report["collision_contract"] = collision_contract(snapshot)
    report["signal_inventory_pre_capture"] = context["signal_inventory"]
    report["network_visual_context"] = {
        "physical_certified_lane_count": len(context["lanes"]),
        "visible_city_sample_proxy_count": len(context["proxies"]),
        "world_bounds_min": vector_json(context["bounds"]["minimum"]),
        "world_bounds_max": vector_json(context["bounds"]["maximum"]),
    }
    if plan is not None and plan.get("_vat_inspection") is not None:
        report["vat_instance_inspection"] = plan["_vat_inspection"]
    now = time.monotonic()
    state = {
        "report": report,
        "paths": paths,
        "preset": preset,
        "plan": plan,
        "context": context,
        "suite": suite_state,
        "world": path_of(world),
        "spawner": path_of(spawner),
        "original_camera": original_camera,
        "camera_applied": False,
        "signal_visibility_records": [],
        "overlay_duration": 0.0,
        "stage": "prepare_visuals",
        "next": now + max(0.0, float(initial_delay)),
        "deadline": now + max(0.0, float(initial_delay)) + TIMEOUT_SECONDS,
        "size": None,
        "follow_next": now,
        "follow_updates": 0,
        "finished": False,
        "handle": None,
    }
    try:
        if plan is not None:
            subsystem.set_level_viewport_camera_info(plan["location"], plan["rotation"])
            state["camera_applied"] = True
            report["camera"] = {
                "location": vector_json(plan["location"]),
                "rotation": rotator_json(plan["rotation"]),
                "target": vector_json(plan["target"]),
                "strategy": plan["strategy"],
                "center_source": plan["center_source"],
                "visible_proxy_count": plan["visible_proxy_count"],
                "visual_target": plan["visual_target"],
            }
        if preset in SIGNAL_HIDDEN_PRESETS:
            state["signal_visibility_records"] = hide_signal_visuals(context, report)
        else:
            report["signal_visual_isolation"] = {
                "requested": False,
                "reason": (
                    "signal-regression must keep all telecom visuals visible"
                    if preset == "signal-regression" else
                    "current-camera compatibility capture does not alter signal visibility"
                ),
                "restoration_attempted": False,
                "restoration_passed": True,
            }
        state["handle"] = unreal.register_slate_post_tick_callback(tick)
        setattr(builtins, CALLBACK_KEY, state["handle"])
        setattr(builtins, STATE_KEY, state)
    except Exception as error:
        finish(state, False, "capture_start_failed", error)
        raise
    unreal.log_warning("OPEN_MASS_CENTRAL_EVIDENCE_STARTED=" + json.dumps({
        "label": safe,
        "preset": preset or "current",
        "world": snapshot["world"],
        "gate": snapshot["gate"],
        "population": snapshot["population"],
        "image": str(paths["png"]),
        "does_not_start_or_stop_pie": True,
    }, ensure_ascii=False, sort_keys=True))


def start_suite(label, suite_name):
    if getattr(builtins, STATE_KEY, None) is not None:
        raise RuntimeError("another Central evidence capture is still active")
    safe = safe_label(label)
    presets = list(SUITES[suite_name])
    directory = report_dir()
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    suite = {
        "name": suite_name,
        "label": label,
        "safe_label": safe,
        "requested_presets": list(presets),
        "remaining": list(presets[1:]),
        "items": [],
        "paths": suite_output_paths(directory, safe, stamp),
        "finished": False,
    }
    first = presets[0]
    try:
        start("{}-{}".format(safe, first), first, suite_state=suite)
    except Exception as error:
        suite["items"].append({
            "preset": first,
            "label": "{}-{}".format(safe, first),
            "status": "FAIL",
            "capture_passed": False,
            "error": "{}: {}".format(type(error).__name__, error),
        })
        finalize_suite(suite, False, "first_capture_start_failed", error)
        raise


def self_test():
    class Asset:
        def get_path_name(self):
            return "/Game/DA_Central"

    class Spawner:
        def __init__(self, mode="CentralCertifiedCache", asset=True, path="/PIE/Spawner"):
            self.mode, self.asset, self.path = mode, Asset() if asset else None, path

        def get_path_name(self):
            return self.path

        def get_network_mode(self):
            return self.mode

        def get_editor_property(self, name):
            if name == "central_network_asset":
                return self.asset
            if name == "network_mode":
                return self.mode
            raise AttributeError(name)

    class World:
        def __init__(self, path, kind=None):
            self.path, self.kind = path, kind

        def get_path_name(self):
            return self.path

        def get_world_type(self):
            if self.kind is None:
                raise AttributeError("no world type")
            return self.kind

    assert safe_label(" Gate 300 / Wide ") == "Gate-300-Wide"
    assert safe_label("中环 Gate300") == "中环-Gate300"
    for value in ("", "...", "CON"):
        try:
            safe_label(value)
        except ValueError:
            pass
        else:
            raise AssertionError("unsafe label accepted")
    assert parse_gate("Gate30") == 30 and parse_gate("GATE300") == 300
    assert parse_gate(True) is None and parse_gate(999) is None
    assert central_mode("CentralCertifiedCache") and central_mode(1)
    assert not central_mode("LocalCertifiedPatch")

    central = Spawner()
    assert select_spawner([central]) is central
    for values in (
        [],
        [central, Spawner(path="/PIE/Other")],
        [Spawner(mode="LocalCertifiedPatch")],
        [Spawner(asset=False)],
    ):
        try:
            select_spawner(values)
        except RuntimeError:
            pass
        else:
            raise AssertionError("invalid spawner set accepted")
    assert pie_world(World("/Game/Map", "WorldType.PIE"))
    assert pie_world(World("/Game/UEDPIE_0_shanghai.shanghai"))
    assert not pie_world(World("/Game/shanghai.shanghai"))

    header = PNG_SIGNATURE + b"\x00\x00\x00\x0dIHDR" + struct.pack(">II", WIDTH, HEIGHT)
    assert png_dimensions_from_header(header) == (WIDTH, HEIGHT)
    names = output_paths(Path("X"), "gate300-wide", "STAMP")
    assert names["png"].name == "central_crowd_evidence_gate300-wide_STAMP.png"
    assert names["latest_png"].name == "central_crowd_evidence_gate300-wide_latest.png"
    suite_names = suite_output_paths(Path("X"), "central-final", "STAMP")
    assert suite_names["json"].name == (
        "central_crowd_evidence_suite_central-final_STAMP.json"
    )
    assert signal_label_kind("SIG_Source_02_Direct_Roof") == ("source", None)
    assert signal_label_kind("SIG_Ray_003_Segment_01_Yellow") == (
        "ray", "Yellow"
    )
    assert signal_label_kind("Unrelated") == (None, None)
    assert evenly_spaced_indices(0, 10) == []
    assert evenly_spaced_indices(3, 10) == [0, 1, 2]
    sampled = evenly_spaced_indices(100, 7)
    assert len(sampled) == 7 and sampled[0] == 0 and sampled[-1] == 99
    assert "signal-regression" not in SIGNAL_HIDDEN_PRESETS
    assert set(SUITES["final"]) == {
        "wide", "street", "route-overlay", "crossing",
        "lod-high", "lod-low", "lod-vat", "telemetry", "signal-regression",
    }
    safe_collision = collision_contract({"metrics": {
        "minimum_planned_spawn_clearance_cm": 20.0,
        "minimum_center_distance_cm": 20.0,
        "minimum_observed_center_distance_cm": 19.5,
        "severe_overlap_pairs": 0,
        "severe_overlap_agents": 0,
        "peak_severe_overlap_pairs": 2,
        "peak_severe_overlap_agents": 3,
        "severe_overlap_pair_observations": 7,
    }})
    assert safe_collision["hard_snapshot_checks_passed"]
    assert not safe_collision["quality_diagnostics"][
        "planned_spawn_clearance_quality_target_met"
    ]
    unsafe_snapshot = {"metrics": {
        "minimum_planned_spawn_clearance_cm": 55.0,
        "minimum_center_distance_cm": 19.99,
        "minimum_observed_center_distance_cm": 19.99,
        "severe_overlap_pairs": 0,
        "severe_overlap_agents": 0,
        "peak_severe_overlap_pairs": 0,
        "peak_severe_overlap_agents": 0,
        "severe_overlap_pair_observations": 0,
    }}
    assert not collision_contract(unsafe_snapshot)["hard_snapshot_checks_passed"]

    source = Path(__file__).read_text(encoding="utf-8").lower()
    forbidden = ["".join(parts) for parts in (
        ("editor", "_play"),
        ("request_end", "_play_map"),
        ("save_current", "_level"),
        ("set_editor", "_property"),
        ("spawn_mass", "_population"),
    )]
    assert not [item for item in forbidden if item in source]
    assert source.endswith("\n")
    assert all(line.rstrip() == line for line in source.splitlines())
    print(json.dumps({
        "status": "PASS",
        "presets": list(PRESETS),
        "suites": {name: list(presets) for name, presets in SUITES.items()},
        "final_suite_capture_count": len(SUITES["final"]),
        "signal_inventory_expected": {
            "sources": SIGNAL_EXPECTED_SOURCES,
            "geometries": SIGNAL_EXPECTED_GEOMETRIES,
            "per_color": SIGNAL_EXPECTED_PER_COLOR,
        },
        "temporary_signal_visibility_requires_restore": True,
        "telemetry_hud_injected": False,
        "required_gate_runtime_report_schema_version": (
            REQUIRED_GATE_RUNTIME_REPORT_SCHEMA_VERSION
        ),
        "collision_contract": {
            "required_gate_verifier_check_name": REQUIRED_COLLISION_CHECK_NAME,
            "severe_overlap_threshold_cm": SEVERE_OVERLAP_THRESHOLD_CM,
            "planned_spawn_quality_target_cm": (
                PLANNED_CENTER_CLEARANCE_QUALITY_TARGET_CM
            ),
            "planned_spawn_quality_target_is_advisory": True,
        },
        "mock_spawner_fail_closed_cases": 4,
        "mock_pie_world_checks": 3,
        "png_dimensions": [WIDTH, HEIGHT],
        "starts_or_stops_pie": False,
    }, ensure_ascii=False, sort_keys=True))


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label")
    capture = parser.add_mutually_exclusive_group()
    capture.add_argument("--preset", choices=PRESETS)
    capture.add_argument("--suite", choices=tuple(SUITES))
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)
    if not args.self_test and not args.label:
        parser.error("--label is required unless --self-test is used")
    if args.self_test and (args.label or args.preset or args.suite):
        parser.error("--self-test cannot be combined with capture arguments")
    return args


def main(argv=None):
    args = parse_args(argv)
    if args.self_test:
        self_test()
    elif unreal is None:
        raise RuntimeError("run this capture inside Unreal Editor through UnrealMCP")
    elif args.suite:
        start_suite(args.label, args.suite)
    else:
        start(args.label, args.preset)


if __name__ == "__main__":
    # Return normally after registering the async callback on the MCP thread.
    main()
