import json
import importlib
import os
import re
import sys
import traceback

import unreal


PROJECT_ROOT = os.path.abspath(unreal.Paths.project_dir())
SCRIPT_DIR = os.path.join(PROJECT_ROOT, "Scripts", "SignalRayDemo")
CONFIG_PATH = os.path.join(PROJECT_ROOT, "Config", "SignalSimulation", "default_scenario.json")
OUTPUT_DIR = os.path.join(PROJECT_ROOT, "Saved", "SignalSimulation")
OUTPUT_PATH = os.path.join(OUTPUT_DIR, "latest-frame.json")
sys.path.insert(0, SCRIPT_DIR)

import signal_simulation  # noqa: E402

signal_simulation = importlib.reload(signal_simulation)
simulate = signal_simulation.simulate


def vector(values):
    return unreal.Vector(float(values[0]), float(values[1]), float(values[2]))


def tuple_from_vector(value):
    return (float(value.x), float(value.y), float(value.z))


def hit_property(hit, name, default=None):
    try:
        return getattr(hit, name)
    except Exception:
        try:
            return hit.get_editor_property(name)
        except Exception:
            return default


def load_settings():
    with open(CONFIG_PATH, "r", encoding="utf-8") as handle:
        settings = json.load(handle)
    if settings.get("schema_version") != "telecom-twin.signal-scenario/1.0":
        raise RuntimeError("Unsupported signal scenario schema_version")
    return settings


def get_manager(actor_subsystem):
    manager_class = getattr(unreal, "SignalRayManager", None)
    if manager_class is None:
        raise RuntimeError("SignalRayManager class is not loaded; rebuild and restart the editor")
    managers = [
        actor for actor in actor_subsystem.get_all_level_actors()
        if actor.get_class().get_name() == "SignalRayManager"
    ]
    manager = managers[0] if managers else actor_subsystem.spawn_actor_from_class(manager_class, unreal.Vector())
    for duplicate in managers[1:]:
        actor_subsystem.destroy_actor(duplicate)
    manager.set_actor_label("SignalRayManager")
    return manager


def get_transmitters(actor_subsystem, settings):
    pattern = re.compile(settings["transmitter_label_pattern"])
    actors = sorted(
        (actor for actor in actor_subsystem.get_all_level_actors() if pattern.fullmatch(actor.get_actor_label())),
        key=lambda actor: actor.get_actor_label(),
    )
    if len(actors) != 12:
        raise RuntimeError("Expected exactly 12 BS_*_top transmitters, found {}".format(len(actors)))
    return [
        {"id": actor.get_actor_label(), "position_cm": tuple_from_vector(actor.get_actor_location())}
        for actor in actors
    ]


def rebuild_collision_proxies(manager, proxies):
    manager.clear_collision_proxies()
    for proxy in proxies:
        manager.add_collision_proxy(
            vector(proxy["center_cm"]),
            vector(proxy["extent_cm"]),
            unreal.Rotator(0.0, 0.0, 0.0),
            unreal.Name(proxy["id"]),
        )


def rebuild_visualization(manager, segments):
    manager.clear_visualization()
    continuous_material = unreal.EditorAssetLibrary.load_asset(
        "/Game/SignalRayDemo/Materials/M_SignalRayContinuous"
    )
    if continuous_material is None:
        raise RuntimeError("M_SignalRayContinuous is missing; run ensure_continuous_material.py once")
    for component in (
        manager.high_strength_rays,
        manager.medium_strength_rays,
        manager.low_strength_rays,
        manager.reflection_nodes,
    ):
        component.set_material(0, continuous_material)
    node_count = 0
    for segment in segments:
        start = vector(segment["start_tuple"])
        end = vector(segment["end_tuple"])
        manager.add_ray_segment(
            start,
            end,
            float(segment["normalized_strength"]),
            int(segment["bounce_index"]),
            unreal.Name(segment["source_id"]),
        )
        if segment["reflection_hit"]:
            manager.add_reflection_node(end, float(segment["normalized_strength"]), int(segment["bounce_index"]))
            node_count += 1
    return node_count


def verify_unreal_proxy_collision(world, manager, proxies):
    failures = []
    component = manager.building_collision_proxies
    for proxy in proxies:
        center = tuple(proxy["center_cm"])
        extent = tuple(proxy["extent_cm"])
        start = unreal.Vector(center[0] - extent[0] * 2.0, center[1], center[2])
        end = unreal.Vector(center[0] + extent[0] * 2.0, center[1], center[2])
        hit = component.line_trace_component(start, end, False, False, False)
        if hit is None:
            failures.append(proxy["id"])
    return failures


def position_overview_camera():
    camera_location = unreal.Vector(0.0, -165000.0, 95000.0)
    target = unreal.Vector(0.0, 0.0, 7000.0)
    rotation = unreal.MathLibrary.find_look_at_rotation(camera_location, target)
    unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).set_level_viewport_camera_info(
        camera_location,
        rotation,
    )


def main():
    settings = load_settings()
    actor_subsystem = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    editor_subsystem = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem)
    world = editor_subsystem.get_editor_world()
    manager = get_manager(actor_subsystem)
    transmitters = get_transmitters(actor_subsystem, settings)
    proxies = settings["collision_proxies"]

    frame, segments, penetrations = simulate(settings, transmitters, proxies)
    if penetrations:
        raise RuntimeError("Analytic zero-penetration validation failed: {}".format(penetrations[:5]))

    rebuild_collision_proxies(manager, proxies)
    node_count = rebuild_visualization(manager, segments)
    unreal_collision_failures = verify_unreal_proxy_collision(world, manager, proxies)
    if unreal_collision_failures:
        raise RuntimeError("Unreal collision proxy validation failed: {}".format(unreal_collision_failures))

    frame["metrics"]["unreal_collision_proxy_failures"] = 0
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as handle:
        json.dump(frame, handle, ensure_ascii=False, indent=2)

    position_overview_camera()
    unreal.EditorLoadingAndSavingUtils.save_dirty_packages(True, True)
    print(json.dumps({
        "frame_path": OUTPUT_PATH,
        "manager_count": 1,
        "transmitter_count": len(transmitters),
        "collision_proxy_count": manager.get_collision_proxy_count(),
        "ray_segment_count": manager.get_ray_segment_count(),
        "reflection_node_count": node_count,
        "zero_penetration_violations": 0,
        "unreal_collision_proxy_failures": 0,
    }, ensure_ascii=False, indent=2))


try:
    main()
except Exception:
    unreal.log_error(traceback.format_exc())
    raise
