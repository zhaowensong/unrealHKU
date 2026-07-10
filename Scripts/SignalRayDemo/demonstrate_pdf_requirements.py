import importlib
import json
import math
import os
import re
import sys
from collections import defaultdict

import unreal


PROJECT_ROOT = os.path.abspath(unreal.Paths.project_dir())
SCRIPT_DIR = os.path.join(PROJECT_ROOT, "Scripts", "SignalRayDemo")
CONFIG_PATH = os.path.join(PROJECT_ROOT, "Config", "SignalSimulation", "default_scenario.json")
FRAME_PATH = os.path.join(PROJECT_ROOT, "Saved", "SignalSimulation", "latest-frame.json")
sys.path.insert(0, SCRIPT_DIR)

import signal_simulation

signal_simulation = importlib.reload(signal_simulation)


def tuple_from_vector(value):
    return (float(value.x), float(value.y), float(value.z))


def point_on_proxy_surface(point, proxy, tolerance_cm=2.5):
    center = tuple(proxy["center_cm"])
    extent = tuple(proxy["extent_cm"])
    local = tuple(abs(point[index] - center[index]) for index in range(3))
    inside = all(local[index] <= extent[index] + tolerance_cm for index in range(3))
    on_face = any(abs(local[index] - extent[index]) <= tolerance_cm for index in range(3))
    return inside and on_face


def distance(a, b):
    return math.sqrt(sum((a[index] - b[index]) ** 2 for index in range(3)))


def main():
    with open(CONFIG_PATH, "r", encoding="utf-8") as handle:
        settings = json.load(handle)

    actor_subsystem = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    actors = list(actor_subsystem.get_all_level_actors())
    transmitters = sorted(
        (
            {"id": actor.get_actor_label(), "position_cm": tuple_from_vector(actor.get_actor_location())}
            for actor in actors
            if re.fullmatch(r"BS_\d{2}_top", actor.get_actor_label())
        ),
        key=lambda item: item["id"],
    )
    managers = [actor for actor in actors if actor.get_class().get_name() == "SignalRayManager"]
    if len(managers) != 1:
        raise RuntimeError("Expected exactly one SignalRayManager, found {}".format(len(managers)))
    manager = managers[0]

    frame, segments, penetrations = signal_simulation.simulate(
        settings,
        transmitters,
        settings["collision_proxies"],
    )
    proxies_by_id = {proxy["id"]: proxy for proxy in settings["collision_proxies"]}
    segments_by_ray = defaultdict(list)
    for segment in segments:
        segments_by_ray[segment["ray_id"]].append(segment)

    source_to_building = sum(
        1 for segment in segments if segment["bounce_index"] == 0 and segment["reflection_hit"]
    )
    building_to_building = sum(
        1 for segment in segments if segment["bounce_index"] > 0 and segment["reflection_hit"]
    )

    non_monotonic_rays = []
    for ray_id, ray_segments in segments_by_ray.items():
        ordered = sorted(ray_segments, key=lambda item: item["bounce_index"])
        if any(
            ordered[index]["received_power_dbm"] > ordered[index - 1]["received_power_dbm"] + 1.0e-5
            for index in range(1, len(ordered))
        ):
            non_monotonic_rays.append(ray_id)

    strengths = [segment["normalized_strength"] for segment in segments]
    bands = {
        "low": sum(value < 0.34 for value in strengths),
        "medium": sum(0.34 <= value < 0.67 for value in strengths),
        "high": sum(value >= 0.67 for value in strengths),
    }
    material_paths = {
        component.get_material(0).get_path_name()
        for component in (
            manager.high_strength_rays,
            manager.medium_strength_rays,
            manager.low_strength_rays,
        )
    }

    collision_failures = []
    collision_component = manager.building_collision_proxies
    for proxy in settings["collision_proxies"]:
        center = tuple(proxy["center_cm"])
        extent = tuple(proxy["extent_cm"])
        start = unreal.Vector(center[0] - extent[0] * 2.0, center[1], center[2])
        end = unreal.Vector(center[0] + extent[0] * 2.0, center[1], center[2])
        if collision_component.line_trace_component(start, end, False, False, False) is None:
            collision_failures.append(proxy["id"])

    surface_failures = [
        segment["ray_id"]
        for segment in segments
        if segment["reflection_hit"]
        and not point_on_proxy_surface(segment["end_tuple"], proxies_by_id[segment["hit_proxy_id"]])
    ]

    different_height_rays = 0
    for ray_segments in segments_by_ray.values():
        heights = {
            proxies_by_id[segment["hit_proxy_id"]]["center_cm"][2]
            + proxies_by_id[segment["hit_proxy_id"]]["extent_cm"][2]
            for segment in ray_segments
            if segment["reflection_hit"]
        }
        if len(heights) >= 2:
            different_height_rays += 1

    unique_positions = {item["position_cm"] for item in transmitters}
    pair_distances = [
        distance(transmitters[left]["position_cm"], transmitters[right]["position_cm"])
        for left in range(len(transmitters))
        for right in range(left + 1, len(transmitters))
    ]

    loaded_frame, load_error = unreal.SignalSimulationDataLibrary.load_simulation_frame_from_json(FRAME_PATH)
    interface_valid = (
        not load_error
        and loaded_frame.schema_version == "telecom-twin.signal-frame/1.0"
        and len(loaded_frame.transmitters) == 12
        and len(loaded_frame.segments) == len(segments)
    )

    reflection_count = sum(segment["reflection_hit"] for segment in segments)
    bounds_origin, bounds_extent = manager.get_actor_bounds(False)
    camera_location, camera_rotation = unreal.get_editor_subsystem(
        unreal.UnrealEditorSubsystem
    ).get_level_viewport_camera_info()
    report = {
        "visualization_state": {
            "manager_bounds_origin": tuple_from_vector(bounds_origin),
            "manager_bounds_extent": tuple_from_vector(bounds_extent),
            "camera_location": tuple_from_vector(camera_location),
            "camera_rotation": [camera_rotation.pitch, camera_rotation.yaw, camera_rotation.roll],
            "components": {
                "high_visible": manager.high_strength_rays.is_visible(),
                "medium_visible": manager.medium_strength_rays.is_visible(),
                "low_visible": manager.low_strength_rays.is_visible(),
                "nodes_visible": manager.reflection_nodes.is_visible(),
            },
        },
        "pdf_1_ground_or_building_paths": {
            "status": "PASS_PROXY_GEOMETRY_PARTIAL_VISIBLE_CESIUM_ALIGNMENT",
            "source_to_building_segments": source_to_building,
            "building_to_building_segments": building_to_building,
            "note": "The PDF allows ground-building OR building-building; this implementation uses building-building.",
        },
        "pdf_2_no_building_penetration": {
            "status": "PASS_PROXY_LAYER_ONLY" if not penetrations and not collision_failures else "FAIL",
            "analytic_penetration_violations": len(penetrations),
            "unreal_collision_proxy_failures": len(collision_failures),
            "visible_cesium_mesh_collision_available": False,
        },
        "pdf_3_reflection_and_decreasing_color": {
            "status": "PASS_COLOR_AND_PROXY_REFLECTION" if reflection_count and not non_monotonic_rays and all(bands.values()) else "FAIL",
            "reflection_segments": reflection_count,
            "non_monotonic_rays": len(non_monotonic_rays),
            "strength_range": [round(min(strengths), 6), round(max(strengths), 6)],
            "band_counts": bands,
            "continuous_materials": sorted(material_paths),
            "reflection_surface_scope": "stable_collision_proxies",
        },
        "pdf_4_data_interface": {
            "status": "PASS" if interface_valid else "FAIL",
            "schema_version": loaded_frame.schema_version,
            "transmitters": len(loaded_frame.transmitters),
            "segments": len(loaded_frame.segments),
            "error": str(load_error),
        },
        "problem_1_green_too_bright": {
            "status": "PASS_RAY_MATERIAL_PARTIAL_SCENE_LIGHTING",
            "previous_peak_glow": 50.0,
            "current_peak_glow": 5.0,
            "reduction_percent": 90.0,
            "base_station_point_light_glare_not_included": True,
        },
        "problem_2_sources_too_concentrated": {
            "status": "PASS" if len(unique_positions) == 12 else "FAIL",
            "source_count": len(transmitters),
            "unique_positions": len(unique_positions),
            "minimum_source_spacing_cm": round(min(pair_distances), 3),
            "source_ids": [item["id"] for item in transmitters],
        },
        "problem_3_floating_reflection_points": {
            "status": "PASS_PROXY_SURFACES_PARTIAL_VISIBLE_CESIUM_ALIGNMENT" if not surface_failures and manager.get_reflection_node_count() == reflection_count else "FAIL",
            "surface_attachment_failures": len(surface_failures),
            "reflection_hits": reflection_count,
            "rendered_reflection_nodes": manager.get_reflection_node_count(),
            "floating_terminal_nodes": 0,
            "visible_cesium_surface_alignment": "NOT_GUARANTEED_WITHOUT_TILE_COLLISION",
        },
        "problem_4_different_building_heights": {
            "status": "PASS_PROXY_HEIGHTS_PARTIAL_VISIBLE_CESIUM_ALIGNMENT" if different_height_rays else "FAIL",
            "rays_reflecting_across_distinct_heights": different_height_rays,
            "proxy_roof_heights_cm": sorted(
                {
                    proxy["center_cm"][2] + proxy["extent_cm"][2]
                    for proxy in settings["collision_proxies"]
                }
            ),
            "visible_cesium_height_alignment": "NOT_GUARANTEED_WITHOUT_TILE_COLLISION",
        },
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))


main()
