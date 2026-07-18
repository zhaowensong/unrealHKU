"""Verify the 30-person City Sample crowd in a running TelecomTwin PIE world.

Run this script through ``run_unreal_python_via_mcp.py`` before or during PIE.
It waits for the PIE world and the City Sample actor representations, samples
their positions for six seconds, traces every measured actor back to a loaded
Cesium triangle, and writes a machine-readable report under ``Saved/Reports``.

The verifier is deliberately read-only with respect to the level and assets.
It does not start/stop PIE, move the editor camera, rebuild the crowd, or save
the map.  A cold City Sample cache can take several minutes to become ready,
so the default wait timeout is intentionally generous.
"""

from __future__ import annotations

import builtins
import hashlib
import json
import math
import re
import statistics
import time
import traceback
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import unreal


TARGET_POPULATION = 30
MIN_UNIQUE_APPEARANCES = 6
MOTION_SAMPLE_SECONDS = 6.0
MIN_MOVED_ACTORS = 24
MIN_MOVEMENT_CM = 60.0
MIN_MEDIAN_MOVEMENT_CM = 100.0

EXPECTED_ROOT_GROUND_OFFSET_CM = 2.0
ROOT_GROUND_TOLERANCE_CM = 8.0
MIN_FOOT_GROUNDED_ACTORS = 27
MIN_FOOT_OFFSET_CM = -30.0
MAX_FOOT_OFFSET_CM = 45.0
MIN_WALKABLE_NORMAL_Z = 0.72
TRACE_HEIGHT_CM = 1600.0
TRACE_DEPTH_CM = 2600.0
CESIUM_GROUND_PROBE_OFFSETS_CM = (
    (0.0, 0.0),
    (18.0, 0.0),
    (-18.0, 0.0),
    (0.0, 18.0),
    (0.0, -18.0),
    (18.0, 18.0),
    (18.0, -18.0),
    (-18.0, 18.0),
    (-18.0, -18.0),
)

EXPECTED_SIGNAL_SOURCES = 30
EXPECTED_SIGNAL_RAY_GEOMETRIES = 1920
EXPECTED_SIGNAL_GEOMETRIES_PER_COLOR = 480
SIGNAL_COLORS = ("Green", "Yellow", "Orange", "Red")
SIGNAL_SOURCE_LABEL = re.compile(r"^SIG_Source_\d{2}_Direct_Roof$")
SIGNAL_RAY_LABEL = re.compile(
    r"^SIG_Ray_\d{3}_(?:Segment|RoofHit)_\d{2}_(Green|Yellow|Orange|Red)$"
)

POLL_INTERVAL_SECONDS = 0.25
WAIT_TIMEOUT_SECONDS = 1800.0
CALLBACK_KEY = "_hk_open_mass_city_sample_verify_handle"
STATE_KEY = "_hk_open_mass_city_sample_verify_state"
REPORT_BASENAME = "open_mass_city_sample_runtime"


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def object_path(value):
    if value is None:
        return None
    try:
        return value.get_path_name()
    except Exception:
        return str(value)


def class_name(value):
    if value is None:
        return None
    try:
        return value.get_class().get_name()
    except Exception:
        return type(value).__name__


def actor_name(actor):
    try:
        return actor.get_name()
    except Exception:
        return object_path(actor) or "<unknown-actor>"


def actor_label(actor):
    try:
        return actor.get_actor_label()
    except Exception:
        return actor_name(actor)


def vector_list(value):
    return [round(float(value.x), 3), round(float(value.y), 3), round(float(value.z), 3)]


def append_error(state, stage, error):
    errors = state.setdefault("errors", [])
    if len(errors) >= 100:
        return
    error_text = repr(error)
    if any(item["stage"] == stage and item["error"] == error_text for item in errors):
        return
    errors.append(
        {
            "stage": stage,
            "error": error_text,
            "traceback": traceback.format_exc().replace(chr(10), " | "),
        }
    )


def get_pie_world():
    subsystem = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem)
    world = subsystem.get_game_world()
    if world is not None:
        return world

    # Retained only as a compatibility fallback for UE Python builds where the
    # subsystem binding is absent even though EditorLevelLibrary is available.
    get_game_world = getattr(unreal.EditorLevelLibrary, "get_game_world", None)
    if get_game_world is not None:
        return get_game_world()
    return None


def actors_of_class(world, actor_class):
    if world is None or actor_class is None:
        return []
    return list(unreal.GameplayStatics.get_all_actors_of_class(world, actor_class))


def all_world_actors(world):
    return actors_of_class(world, unreal.Actor)


def child_actor_for_proxy(proxy):
    components = proxy.get_components_by_class(unreal.ChildActorComponent)
    for component in components:
        try:
            child = component.get_editor_property("child_actor")
        except Exception:
            child = None
        if child is not None:
            return child

    # GetAttachedActors is a Blueprint/Python-exposed UFUNCTION and provides a
    # safe fallback if a particular engine build hides the private ChildActor
    # property from Python reflection.
    try:
        attached_actors = proxy.get_attached_actors()
    except Exception:
        attached_actors = []
    for child in attached_actors:
        if class_name(child) == "BP_CrowdCharacter_C":
            return child
    return None


def skeletal_mesh_for(component):
    getter = getattr(component, "get_skeletal_mesh_asset", None)
    if getter is not None:
        try:
            return getter()
        except Exception:
            pass
    for property_name in ("skeletal_mesh_asset", "skeletal_mesh"):
        try:
            return component.get_editor_property(property_name)
        except Exception:
            pass
    return None


def visible_skeletal_components(child):
    result = []
    for component in child.get_components_by_class(unreal.SkeletalMeshComponent):
        mesh = skeletal_mesh_for(component)
        try:
            visible = bool(component.is_visible())
        except Exception:
            visible = False
        if visible and mesh is not None:
            result.append(component)
    return result


def appearance_signature(components):
    mesh_paths = sorted(
        path
        for path in (object_path(skeletal_mesh_for(component)) for component in components)
        if path
    )
    if not mesh_paths:
        return None, None
    raw_signature = "|".join(mesh_paths)
    digest = hashlib.sha1(raw_signature.encode("utf-8")).hexdigest()[:12]
    return digest, mesh_paths


def driving_mesh_for(child):
    components = list(child.get_components_by_class(unreal.SkeletalMeshComponent))
    for component in components:
        if component.get_name() == "SkeletalMeshComponent0":
            return component
    return components[0] if components else None


def primitive_collision_disabled(component):
    query_enabled = bool(component.is_query_collision_enabled())
    physics_getter = getattr(component, "is_physics_collision_enabled", None)
    physics_enabled = bool(physics_getter()) if physics_getter is not None else False
    overlap_getter = getattr(component, "get_generate_overlap_events", None)
    overlap_enabled = bool(overlap_getter()) if overlap_getter is not None else False
    return not query_enabled and not physics_enabled and not overlap_enabled


def collect_runtime_snapshot(world, state):
    spawner_class = getattr(unreal, "OpenMassCrowdSpawner", None)
    proxy_class = getattr(unreal, "OpenMassCrowdCitySampleActor", None)
    spawners = actors_of_class(world, spawner_class)
    proxies = actors_of_class(world, proxy_class)

    spawned_entity_counts = []
    requested_population_counts = []
    for spawner in spawners:
        try:
            spawned_entity_counts.append(int(spawner.get_spawned_entity_count()))
        except Exception as error:
            append_error(state, "spawner.get_spawned_entity_count", error)
        try:
            requested_population_counts.append(
                int(spawner.get_editor_property("population_count"))
            )
        except Exception as error:
            append_error(state, "spawner.population_count", error)

    proxy_records = []
    official_children_by_path = {}
    appearance_counts = Counter()
    appearance_meshes = {}
    animation_playing_count = 0
    driving_mesh_count = 0
    visible_city_sample_count = 0
    collision_disabled_child_count = 0
    primitive_component_count = 0
    collision_enabled_primitive_count = 0

    for proxy in sorted(proxies, key=actor_name):
        try:
            proxy_hidden = bool(proxy.get_editor_property("hidden"))
        except Exception:
            proxy_hidden = False
        child = child_actor_for_proxy(proxy)
        child_class = class_name(child)
        official_child = child is not None and child_class == "BP_CrowdCharacter_C"
        try:
            child_hidden = bool(child.get_editor_property("hidden")) if child else False
        except Exception:
            child_hidden = False
        components = visible_skeletal_components(child) if child is not None else []
        signature, mesh_paths = appearance_signature(components)
        if (
            signature is not None
            and official_child
            and not proxy_hidden
            and not child_hidden
        ):
            appearance_counts[signature] += 1
            appearance_meshes.setdefault(signature, mesh_paths)

        driving_mesh = driving_mesh_for(child) if child is not None else None
        animation_playing = False
        play_rate = None
        animation_position = None
        if driving_mesh is not None:
            if official_child and not proxy_hidden and not child_hidden:
                driving_mesh_count += 1
            try:
                animation_playing = bool(driving_mesh.is_playing())
                play_rate = round(float(driving_mesh.get_play_rate()), 4)
                animation_position = round(float(driving_mesh.get_position()), 4)
            except Exception as error:
                append_error(state, "skeletal_animation_state", error)
        if animation_playing and official_child and not proxy_hidden and not child_hidden:
            animation_playing_count += 1

        primitives = (
            list(child.get_components_by_class(unreal.PrimitiveComponent))
            if child is not None
            else []
        )
        track_collision = official_child and not proxy_hidden and not child_hidden
        child_collision_disabled = bool(primitives)
        for component in primitives:
            if track_collision:
                primitive_component_count += 1
            try:
                disabled = primitive_collision_disabled(component)
            except Exception as error:
                append_error(state, "primitive_collision_state", error)
                disabled = False
            if not disabled:
                if track_collision:
                    collision_enabled_primitive_count += 1
                child_collision_disabled = False
        if child_collision_disabled and track_collision:
            collision_disabled_child_count += 1

        if official_child:
            official_children_by_path[object_path(child)] = child
        if official_child and components and not proxy_hidden and not child_hidden:
            visible_city_sample_count += 1

        location = proxy.get_actor_location()
        proxy_records.append(
            {
                "proxy": proxy,
                "proxy_name": actor_name(proxy),
                "proxy_path": object_path(proxy),
                "location": vector_list(location),
                "proxy_hidden_in_game": proxy_hidden,
                "child_hidden_in_game": child_hidden,
                "child": child,
                "child_class": child_class,
                "official_child": official_child,
                "visible_skeletal_component_count": len(components),
                "appearance_signature": signature,
                "animation_playing": animation_playing,
                "play_rate": play_rate,
                "animation_position": animation_position,
                "collision_disabled": child_collision_disabled,
            }
        )

    # Do not scan the thousands of signal/photogrammetry actors on every poll.
    # The component property plus attached-actor fallback above already proves
    # the stricter one-to-one proxy/child relationship.
    direct_official_children = list(official_children_by_path.values())

    active_records = [
        record
        for record in proxy_records
        if record["official_child"]
        and record["visible_skeletal_component_count"] > 0
        and not record["proxy_hidden_in_game"]
        and not record["child_hidden_in_game"]
    ]
    positions = {
        record["proxy_name"]: record["location"] for record in active_records
    }

    return {
        "world": object_path(world),
        "world_name": world.get_name(),
        "spawner_count": len(spawners),
        "spawner_labels": [actor_label(spawner) for spawner in spawners],
        "requested_population_counts": requested_population_counts,
        "spawned_entity_counts": spawned_entity_counts,
        "spawned_entity_count_total": sum(spawned_entity_counts),
        "proxy_actor_count": len(proxies),
        "active_proxy_actor_count": len(active_records),
        "proxy_records": proxy_records,
        "paired_official_child_count": len(official_children_by_path),
        "direct_official_child_count": len(direct_official_children),
        "visible_city_sample_count": visible_city_sample_count,
        "driving_mesh_count": driving_mesh_count,
        "animation_playing_count": animation_playing_count,
        "unique_appearance_count": len(appearance_counts),
        "appearance_counts": dict(sorted(appearance_counts.items())),
        "appearance_meshes": appearance_meshes,
        "collision_disabled_child_count": collision_disabled_child_count,
        "primitive_component_count": primitive_component_count,
        "collision_enabled_primitive_count": collision_enabled_primitive_count,
        "positions": positions,
    }


def snapshot_ready(snapshot):
    population_ready = (
        snapshot["spawned_entity_count_total"] >= TARGET_POPULATION
        or snapshot["active_proxy_actor_count"] >= TARGET_POPULATION
    )
    return (
        snapshot["spawner_count"] == 1
        and population_ready
        and snapshot["active_proxy_actor_count"] >= TARGET_POPULATION
        and snapshot["paired_official_child_count"] >= TARGET_POPULATION
        and snapshot["visible_city_sample_count"] >= TARGET_POPULATION
    )


def distance_xy(first, second):
    return math.hypot(second[0] - first[0], second[1] - first[1])


def movement_report(start_positions, end_positions, sample_seconds):
    common_names = sorted(set(start_positions) & set(end_positions))
    distances = {
        name: round(distance_xy(start_positions[name], end_positions[name]), 3)
        for name in common_names
    }
    values = list(distances.values())
    moved_count = sum(distance >= MIN_MOVEMENT_CM for distance in values)
    return {
        "sample_seconds": round(sample_seconds, 3),
        "common_actor_count": len(common_names),
        "moved_over_60cm_count": moved_count,
        "minimum_cm": round(min(values), 3) if values else None,
        "median_cm": round(float(statistics.median(values)), 3) if values else None,
        "maximum_cm": round(max(values), 3) if values else None,
        "per_actor_xy_cm": distances,
    }


def collect_cesium_components(world):
    components = []
    tileset_class = getattr(unreal, "Cesium3DTileset", None)
    if tileset_class is not None:
        tileset_actors = actors_of_class(world, tileset_class)
    else:
        tileset_actors = [
            actor for actor in all_world_actors(world) if class_name(actor) == "Cesium3DTileset"
        ]
    for actor in tileset_actors:
        for component in actor.get_components_by_class(unreal.PrimitiveComponent):
            if component.get_class().get_name() != "CesiumGltfPrimitiveComponent":
                continue
            try:
                accepted = component.is_visible() and component.is_query_collision_enabled()
            except Exception:
                accepted = False
            if accepted:
                components.append(component)
    return components


def trace_cesium_ground(components, location):
    for offset_x, offset_y in CESIUM_GROUND_PROBE_OFFSETS_CM:
        start = unreal.Vector(
            location[0] + offset_x,
            location[1] + offset_y,
            location[2] + TRACE_HEIGHT_CM,
        )
        end = unreal.Vector(
            location[0] + offset_x,
            location[1] + offset_y,
            location[2] - TRACE_DEPTH_CM,
        )
        nearest = None
        nearest_distance = None
        for component in components:
            hit = component.line_trace_component(start, end, True, False, False)
            if not hit:
                continue
            point, normal, _bone_name, _hit_result = hit
            if normal.z < MIN_WALKABLE_NORMAL_Z:
                continue
            distance = start.z - point.z
            if nearest is None or distance < nearest_distance:
                nearest = (point, normal, component, [offset_x, offset_y])
                nearest_distance = distance
        if nearest is not None:
            return nearest
    return None


def visible_visual_bottom_z(child):
    bottoms = []
    for component in visible_skeletal_components(child):
        origin, extent, _sphere_radius = unreal.SystemLibrary.get_component_bounds(component)
        if extent.z > 0.0:
            bottoms.append(float(origin.z - extent.z))
    return min(bottoms) if bottoms else None


def grounding_report(world, snapshot, state):
    components = collect_cesium_components(world)
    measurements = []
    for record in snapshot["proxy_records"]:
        if not record["official_child"] or record["visible_skeletal_component_count"] <= 0:
            continue
        if record["proxy_hidden_in_game"]:
            continue
        if record["child_hidden_in_game"]:
            continue
        if len(measurements) >= TARGET_POPULATION:
            break
        try:
            hit = trace_cesium_ground(components, record["location"])
        except Exception as error:
            append_error(state, "cesium_ground_trace", error)
            hit = None
        if hit is None:
            measurements.append(
                {
                    "proxy_name": record["proxy_name"],
                    "trace_hit": False,
                    "root_location": record["location"],
                }
            )
            continue

        point, normal, component, probe_offset = hit
        root_offset = record["location"][2] - float(point.z)
        root_error = root_offset - EXPECTED_ROOT_GROUND_OFFSET_CM
        try:
            visual_bottom = visible_visual_bottom_z(record["child"])
        except Exception as error:
            append_error(state, "visible_visual_bounds", error)
            visual_bottom = None
        foot_offset = visual_bottom - float(point.z) if visual_bottom is not None else None
        measurements.append(
            {
                "proxy_name": record["proxy_name"],
                "trace_hit": True,
                "root_location": record["location"],
                "ground_location": vector_list(point),
                "ground_probe_offset_xy_cm": probe_offset,
                "normal_z": round(float(normal.z), 5),
                "component_class": component.get_class().get_name(),
                "root_ground_offset_cm": round(root_offset, 3),
                "root_ground_error_cm": round(root_error, 3),
                "visual_bottom_z": round(visual_bottom, 3)
                if visual_bottom is not None
                else None,
                "visual_foot_ground_offset_cm": round(foot_offset, 3)
                if foot_offset is not None
                else None,
            }
        )

    traced = [item for item in measurements if item["trace_hit"]]
    root_within = [
        item
        for item in traced
        if abs(item["root_ground_error_cm"]) <= ROOT_GROUND_TOLERANCE_CM
    ]
    foot_measured = [
        item for item in traced if item.get("visual_foot_ground_offset_cm") is not None
    ]
    foot_within = [
        item
        for item in foot_measured
        if MIN_FOOT_OFFSET_CM
        <= item["visual_foot_ground_offset_cm"]
        <= MAX_FOOT_OFFSET_CM
    ]
    root_errors = [abs(item["root_ground_error_cm"]) for item in traced]
    foot_offsets = [item["visual_foot_ground_offset_cm"] for item in foot_measured]
    return {
        "cesium_component_count": len(components),
        "requested_actor_count": min(
            TARGET_POPULATION,
            sum(
                1
                for record in snapshot["proxy_records"]
                if record["official_child"]
                and record["visible_skeletal_component_count"] > 0
                and not record["proxy_hidden_in_game"]
                and not record["child_hidden_in_game"]
            ),
        ),
        "trace_hit_count": len(traced),
        "root_within_tolerance_count": len(root_within),
        "root_max_abs_error_cm": round(max(root_errors), 3) if root_errors else None,
        "visual_foot_measured_count": len(foot_measured),
        "visual_foot_within_range_count": len(foot_within),
        "visual_foot_offset_min_cm": round(min(foot_offsets), 3)
        if foot_offsets
        else None,
        "visual_foot_offset_median_cm": round(float(statistics.median(foot_offsets)), 3)
        if foot_offsets
        else None,
        "visual_foot_offset_max_cm": round(max(foot_offsets), 3)
        if foot_offsets
        else None,
        "measurements": measurements,
    }


def visible_static_mesh_geometry_for_actor(actor):
    """Count actual visible mesh geometry, not merely a correctly named actor."""

    total = 0
    assigned_component_count = 0
    visible_component_count = 0
    ism_class = getattr(unreal, "InstancedStaticMeshComponent", None)
    static_mesh_class = getattr(unreal, "StaticMeshComponent", None)
    if static_mesh_class is None:
        return total, assigned_component_count, visible_component_count

    for component in actor.get_components_by_class(static_mesh_class):
        mesh_getter = getattr(component, "get_static_mesh", None)
        mesh = mesh_getter() if mesh_getter is not None else None
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
            total += int(instance_getter()) if instance_getter is not None else 0
        else:
            total += 1

    return total, assigned_component_count, visible_component_count


def signal_report(world, state):
    source_labels = []
    ray_actor_labels = []
    ray_geometry_count = 0
    ray_mesh_component_count = 0
    ray_visible_mesh_component_count = 0
    ray_labels_without_visible_geometry = []
    color_geometry_counts = Counter({color: 0 for color in SIGNAL_COLORS})

    for actor in all_world_actors(world):
        label = actor_label(actor)
        if SIGNAL_SOURCE_LABEL.fullmatch(label):
            source_labels.append(label)
        match = SIGNAL_RAY_LABEL.fullmatch(label)
        if match is None:
            continue

        ray_actor_labels.append(label)
        try:
            (
                geometry_count,
                assigned_component_count,
                visible_component_count,
            ) = visible_static_mesh_geometry_for_actor(actor)
        except Exception as error:
            append_error(state, "signal_ray_visible_geometry", error)
            geometry_count = 0
            assigned_component_count = 0
            visible_component_count = 0
        ray_mesh_component_count += assigned_component_count
        ray_visible_mesh_component_count += visible_component_count
        if geometry_count <= 0:
            ray_labels_without_visible_geometry.append(label)
        ray_geometry_count += geometry_count
        color_geometry_counts[match.group(1)] += geometry_count

    return {
        "source_actor_count": len(source_labels),
        "unique_source_label_count": len(set(source_labels)),
        "source_labels": sorted(source_labels),
        "ray_actor_count": len(ray_actor_labels),
        "unique_ray_label_count": len(set(ray_actor_labels)),
        "ray_geometry_count": ray_geometry_count,
        "ray_mesh_component_count": ray_mesh_component_count,
        "ray_visible_mesh_component_count": ray_visible_mesh_component_count,
        "ray_labels_without_visible_geometry_count": len(
            ray_labels_without_visible_geometry
        ),
        "ray_labels_without_visible_geometry_samples": sorted(
            ray_labels_without_visible_geometry
        )[:40],
        "color_geometry_counts": dict(color_geometry_counts),
        "ray_actor_label_samples": sorted(ray_actor_labels)[:40],
    }


def add_check(checks, name, passed, actual, expected):
    checks[name] = {
        "passed": bool(passed),
        "actual": actual,
        "expected": expected,
    }


def serializable_snapshot(snapshot):
    return {
        key: value
        for key, value in snapshot.items()
        if key not in {"proxy_records", "positions"}
    } | {
        "positions": snapshot.get("positions", {}),
        "proxy_records": [
            {
                key: value
                for key, value in record.items()
                if key not in {"proxy", "child"}
            }
            for record in snapshot.get("proxy_records", [])
        ],
    }


def report_paths():
    saved_dir = Path(
        unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_saved_dir())
    )
    reports_dir = saved_dir / "Reports"
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return (
        reports_dir / (REPORT_BASENAME + "_" + timestamp + ".json"),
        reports_dir / (REPORT_BASENAME + "_latest.json"),
    )


def write_report(report):
    timestamped_path, latest_path = report_paths()
    timestamped_path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + chr(10)
    timestamped_path.write_text(payload, encoding="utf-8")
    latest_path.write_text(payload, encoding="utf-8")
    return timestamped_path, latest_path


def unregister_callback(state):
    handle = state.get("handle")
    if handle is not None:
        try:
            unreal.unregister_slate_post_tick_callback(handle)
        except Exception:
            pass
        state["handle"] = None
    if getattr(builtins, CALLBACK_KEY, None) == handle:
        delattr(builtins, CALLBACK_KEY)
    if getattr(builtins, STATE_KEY, None) is state:
        delattr(builtins, STATE_KEY)


def finalize(state, reason, world=None):
    if state.get("finished"):
        return
    state["finished"] = True
    unregister_callback(state)

    now = time.monotonic()
    final_snapshot = state.get("latest_snapshot")
    try:
        if world is not None:
            final_snapshot = collect_runtime_snapshot(world, state)
    except Exception as error:
        append_error(state, "collect_final_snapshot", error)

    if final_snapshot is None:
        final_snapshot = {
            "world": None,
            "world_name": None,
            "spawner_count": 0,
            "spawner_labels": [],
            "requested_population_counts": [],
            "spawned_entity_counts": [],
            "spawned_entity_count_total": 0,
            "proxy_actor_count": 0,
            "active_proxy_actor_count": 0,
            "proxy_records": [],
            "paired_official_child_count": 0,
            "direct_official_child_count": 0,
            "visible_city_sample_count": 0,
            "driving_mesh_count": 0,
            "animation_playing_count": 0,
            "unique_appearance_count": 0,
            "appearance_counts": {},
            "appearance_meshes": {},
            "collision_disabled_child_count": 0,
            "primitive_component_count": 0,
            "collision_enabled_primitive_count": 0,
            "positions": {},
        }

    sample_started = state.get("sample_started_game_seconds")
    final_game_seconds = (
        float(unreal.GameplayStatics.get_time_seconds(world))
        if world is not None
        else None
    )
    sample_seconds = (
        max(0.0, final_game_seconds - sample_started)
        if sample_started is not None and final_game_seconds is not None
        else 0.0
    )
    movement = movement_report(
        state.get("start_positions", {}),
        final_snapshot.get("positions", {}),
        sample_seconds,
    )

    if world is not None:
        try:
            grounding = grounding_report(world, final_snapshot, state)
        except Exception as error:
            append_error(state, "grounding_report", error)
            grounding = {}
        try:
            signals = signal_report(world, state)
        except Exception as error:
            append_error(state, "signal_report", error)
            signals = {}
    else:
        grounding = {}
        signals = {}

    checks = {}
    world_name = final_snapshot.get("world_name") or ""
    add_check(
        checks,
        "pie_world_is_shanghai",
        world_name.lower() == "shanghai" or world_name.lower().endswith("_shanghai"),
        world_name or None,
        "shanghai (PIE prefix allowed)",
    )
    add_check(
        checks,
        "single_open_mass_spawner",
        final_snapshot.get("spawner_count") == 1,
        final_snapshot.get("spawner_count"),
        1,
    )
    add_check(
        checks,
        "mass_entities_or_proxies_30",
        final_snapshot.get("requested_population_counts") == [TARGET_POPULATION]
        and final_snapshot.get("spawned_entity_counts") == [TARGET_POPULATION]
        and final_snapshot.get("spawned_entity_count_total", 0) == TARGET_POPULATION
        and final_snapshot.get("active_proxy_actor_count", 0) == TARGET_POPULATION,
        {
            "requested": final_snapshot.get("requested_population_counts", []),
            "spawned_per_spawner": final_snapshot.get("spawned_entity_counts", []),
            "mass_entities": final_snapshot.get("spawned_entity_count_total", 0),
            "active_proxies": final_snapshot.get("active_proxy_actor_count", 0),
        },
        {"exact": TARGET_POPULATION},
    )
    add_check(
        checks,
        "city_sample_proxy_actors_30",
        final_snapshot.get("active_proxy_actor_count", 0) == TARGET_POPULATION
        and final_snapshot.get("proxy_actor_count", 0) == TARGET_POPULATION,
        {
            "active": final_snapshot.get("active_proxy_actor_count", 0),
            "total_including_hidden_pool": final_snapshot.get("proxy_actor_count", 0),
        },
        {"exact": TARGET_POPULATION},
    )
    add_check(
        checks,
        "paired_bp_crowd_character_children_30",
        final_snapshot.get("paired_official_child_count", 0) == TARGET_POPULATION,
        final_snapshot.get("paired_official_child_count", 0),
        {"exact": TARGET_POPULATION, "class": "BP_CrowdCharacter_C"},
    )
    add_check(
        checks,
        "visible_city_sample_characters_30",
        final_snapshot.get("visible_city_sample_count", 0) == TARGET_POPULATION,
        final_snapshot.get("visible_city_sample_count", 0),
        {"exact": TARGET_POPULATION},
    )
    add_check(
        checks,
        "walk_animation_playing_30",
        final_snapshot.get("animation_playing_count", 0) == TARGET_POPULATION,
        final_snapshot.get("animation_playing_count", 0),
        {"exact": TARGET_POPULATION, "driving_mesh": "SkeletalMeshComponent0"},
    )
    add_check(
        checks,
        "appearance_variation_6",
        final_snapshot.get("unique_appearance_count", 0) >= MIN_UNIQUE_APPEARANCES,
        final_snapshot.get("unique_appearance_count", 0),
        {"minimum_unique_visible_mesh_combinations": MIN_UNIQUE_APPEARANCES},
    )
    add_check(
        checks,
        "movement_over_6_seconds",
        movement["sample_seconds"] >= MOTION_SAMPLE_SECONDS
        and movement["common_actor_count"] >= MIN_MOVED_ACTORS
        and movement["moved_over_60cm_count"] >= MIN_MOVED_ACTORS
        and movement["median_cm"] is not None
        and movement["median_cm"] >= MIN_MEDIAN_MOVEMENT_CM,
        {
            "sample_seconds": movement["sample_seconds"],
            "common_actor_count": movement["common_actor_count"],
            "moved_over_60cm_count": movement["moved_over_60cm_count"],
            "median_cm": movement["median_cm"],
        },
        {
            "sample_seconds_minimum": MOTION_SAMPLE_SECONDS,
            "moved_actor_minimum": MIN_MOVED_ACTORS,
            "per_actor_movement_cm_minimum": MIN_MOVEMENT_CM,
            "median_movement_cm_minimum": MIN_MEDIAN_MOVEMENT_CM,
        },
    )
    add_check(
        checks,
        "cesium_trace_hits_30",
        grounding.get("trace_hit_count", 0) == TARGET_POPULATION,
        grounding.get("trace_hit_count", 0),
        {"exact": TARGET_POPULATION, "component": "CesiumGltfPrimitiveComponent"},
    )
    add_check(
        checks,
        "mass_root_ground_offset_2cm",
        grounding.get("root_within_tolerance_count", 0) == TARGET_POPULATION,
        {
            "within_tolerance_count": grounding.get("root_within_tolerance_count", 0),
            "max_abs_error_cm": grounding.get("root_max_abs_error_cm"),
        },
        {
            "exact_count": TARGET_POPULATION,
            "target_offset_cm": EXPECTED_ROOT_GROUND_OFFSET_CM,
            "tolerance_cm": ROOT_GROUND_TOLERANCE_CM,
        },
    )
    add_check(
        checks,
        "visible_foot_grounding_27",
        grounding.get("visual_foot_within_range_count", 0) >= MIN_FOOT_GROUNDED_ACTORS,
        {
            "measured_count": grounding.get("visual_foot_measured_count", 0),
            "within_range_count": grounding.get("visual_foot_within_range_count", 0),
            "offset_median_cm": grounding.get("visual_foot_offset_median_cm"),
        },
        {
            "minimum_count": MIN_FOOT_GROUNDED_ACTORS,
            "offset_range_cm": [MIN_FOOT_OFFSET_CM, MAX_FOOT_OFFSET_CM],
        },
    )
    add_check(
        checks,
        "city_sample_visual_collision_disabled",
        final_snapshot.get("collision_disabled_child_count", 0) == TARGET_POPULATION
        and final_snapshot.get("collision_enabled_primitive_count", 0) == 0,
        {
            "disabled_child_count": final_snapshot.get(
                "collision_disabled_child_count", 0
            ),
            "primitive_component_count": final_snapshot.get(
                "primitive_component_count", 0
            ),
            "enabled_primitive_count": final_snapshot.get(
                "collision_enabled_primitive_count", 0
            ),
        },
        {"exact_disabled_children": TARGET_POPULATION, "enabled_primitives": 0},
    )
    add_check(
        checks,
        "signal_sources_preserved_30",
        signals.get("source_actor_count", 0) == EXPECTED_SIGNAL_SOURCES
        and signals.get("unique_source_label_count", 0) == EXPECTED_SIGNAL_SOURCES,
        {
            "actors": signals.get("source_actor_count", 0),
            "unique_labels": signals.get("unique_source_label_count", 0),
        },
        {"exact": EXPECTED_SIGNAL_SOURCES},
    )
    add_check(
        checks,
        "signal_ray_geometry_preserved_1920",
        signals.get("ray_actor_count", 0) == EXPECTED_SIGNAL_RAY_GEOMETRIES
        and signals.get("unique_ray_label_count", 0) == EXPECTED_SIGNAL_RAY_GEOMETRIES
        and signals.get("ray_geometry_count", 0) == EXPECTED_SIGNAL_RAY_GEOMETRIES
        and signals.get("ray_labels_without_visible_geometry_count", 0) == 0,
        {
            "ray_actor_count": signals.get("ray_actor_count", 0),
            "unique_ray_label_count": signals.get("unique_ray_label_count", 0),
            "ray_geometry_count": signals.get("ray_geometry_count", 0),
            "assigned_mesh_components": signals.get("ray_mesh_component_count", 0),
            "visible_mesh_components": signals.get(
                "ray_visible_mesh_component_count", 0
            ),
            "labels_without_visible_geometry": signals.get(
                "ray_labels_without_visible_geometry_count", 0
            ),
        },
        {"exact_geometry_count": EXPECTED_SIGNAL_RAY_GEOMETRIES},
    )
    color_counts = signals.get("color_geometry_counts", {})
    for color in SIGNAL_COLORS:
        add_check(
            checks,
            "signal_color_" + color.lower() + "_preserved_480",
            color_counts.get(color, 0) == EXPECTED_SIGNAL_GEOMETRIES_PER_COLOR,
            color_counts.get(color, 0),
            {"exact": EXPECTED_SIGNAL_GEOMETRIES_PER_COLOR},
        )

    failed_checks = [name for name, value in checks.items() if not value["passed"]]
    # A compatibility fallback can record a diagnostic while a second, known
    # UE API still provides complete evidence.  Required check failures (or an
    # abnormal completion reason) decide the verdict; diagnostics remain fully
    # visible in the report instead of silently turning a valid run red.
    overall_passed = (
        not failed_checks
        and reason == "completed"
        and not state.get("errors", [])
    )
    report = {
        "schema_version": 1,
        "generated_utc": utc_now(),
        "engine_version": unreal.SystemLibrary.get_engine_version(),
        "project_dir": unreal.Paths.convert_relative_path_to_full(
            unreal.Paths.project_dir()
        ),
        "completion_reason": reason,
        "elapsed_seconds": round(now - state["started_monotonic"], 3),
        "overall_passed": overall_passed,
        "failed_checks": failed_checks,
        "checks": checks,
        "movement": movement,
        "grounding": grounding,
        "signals": signals,
        "runtime_snapshot": serializable_snapshot(final_snapshot),
        "diagnostics": {
            "error_count": len(state.get("errors", [])),
            "errors": state.get("errors", []),
            "status_updates": state.get("status_updates", []),
        },
    }

    try:
        timestamped_path, latest_path = write_report(report)
        marker = {
            "overall_passed": overall_passed,
            "completion_reason": reason,
            "failed_checks": failed_checks,
            "error_count": len(state.get("errors", [])),
            "report": str(timestamped_path),
            "latest": str(latest_path),
        }
        message = "OPEN_MASS_CITY_SAMPLE_RUNTIME_VERIFY=" + json.dumps(
            marker, ensure_ascii=False, sort_keys=True
        )
        if overall_passed:
            unreal.log_warning(message)
        else:
            unreal.log_error(message)
    except Exception as error:
        unreal.log_error(
            "OPEN_MASS_CITY_SAMPLE_RUNTIME_REPORT_WRITE_FAILED=" + repr(error)
        )


def add_status_update(state, status, snapshot=None):
    updates = state.setdefault("status_updates", [])
    now = time.monotonic()
    update = {
        "utc": utc_now(),
        "elapsed_seconds": round(now - state["started_monotonic"], 3),
        "status": status,
    }
    if snapshot is not None:
        update["spawners"] = snapshot["spawner_count"]
        update["mass_entities"] = snapshot["spawned_entity_count_total"]
        update["proxies"] = snapshot["proxy_actor_count"]
        update["active_proxies"] = snapshot["active_proxy_actor_count"]
        update["official_children"] = snapshot["paired_official_child_count"]
        update["visible_characters"] = snapshot["visible_city_sample_count"]
    comparison_keys = (
        "status",
        "spawners",
        "mass_entities",
        "proxies",
        "active_proxies",
        "official_children",
        "visible_characters",
    )
    if updates and all(
        updates[-1].get(key) == update.get(key) for key in comparison_keys
    ):
        return
    updates.append(update)
    unreal.log_warning(
        "OPEN_MASS_CITY_SAMPLE_RUNTIME_STATUS="
        + json.dumps(update, ensure_ascii=False, sort_keys=True)
    )


def tick_verifier(_delta_seconds):
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
                add_status_update(state, "pie_ended_before_verification")
                finalize(state, "pie_ended_early", None)
                return
            add_status_update(state, "waiting_for_pie_world")
        else:
            state["pie_seen"] = True
            snapshot = collect_runtime_snapshot(world, state)
            state["latest_snapshot"] = snapshot
            if not snapshot_ready(snapshot):
                add_status_update(state, "waiting_for_30_city_sample_characters", snapshot)
            elif state.get("sample_started_game_seconds") is None:
                state["sample_started_game_seconds"] = float(
                    unreal.GameplayStatics.get_time_seconds(world)
                )
                state["start_positions"] = dict(snapshot["positions"])
                add_status_update(state, "sampling_six_seconds_of_movement", snapshot)
            elif (
                float(unreal.GameplayStatics.get_time_seconds(world))
                - state["sample_started_game_seconds"]
                >= MOTION_SAMPLE_SECONDS
            ):
                add_status_update(state, "finalizing_runtime_evidence", snapshot)
                finalize(state, "completed", world)
                return

        if now - state["started_monotonic"] >= WAIT_TIMEOUT_SECONDS:
            add_status_update(state, "verification_timeout")
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

    now = time.monotonic()
    state = {
        "started_monotonic": now,
        "last_poll_monotonic": 0.0,
        "sample_started_game_seconds": None,
        "start_positions": {},
        "latest_snapshot": None,
        "pie_seen": False,
        "finished": False,
        "errors": [],
        "status_updates": [],
        "handle": None,
    }
    state["handle"] = unreal.register_slate_post_tick_callback(tick_verifier)
    setattr(builtins, CALLBACK_KEY, state["handle"])
    setattr(builtins, STATE_KEY, state)
    unreal.log_warning(
        "OPEN_MASS_CITY_SAMPLE_RUNTIME_VERIFY_STARTED="
        + json.dumps(
            {
                "target_population": TARGET_POPULATION,
                "movement_sample_seconds": MOTION_SAMPLE_SECONDS,
                "wait_timeout_seconds": WAIT_TIMEOUT_SECONDS,
            },
            sort_keys=True,
        )
    )


main()
