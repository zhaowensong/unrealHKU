"""Build a minimal, auditable real-rooftop reflection prototype.

This deliberately avoids the facade-plane fallback used by the full demo.
Every reflection point must come from a complex collision trace against a
visible CesiumGltfPrimitiveComponent, and must have an upward-facing normal.
"""

import json
import math
import os
import traceback

import unreal


CENTER = unreal.Vector(-230857.717, 258000.925, 17905.786)
GRID_STEP = 14000.0
SOURCE_CLEARANCE = 2000.0
ROOFTOP_NORMAL_Z_MIN = 0.80
MIN_BUILDING_ROOFTOP_Z = 9000.0
URBAN_Y_MAX = 275000.0
PREFERRED_SOURCE_XY = unreal.Vector(-188857.717, 244000.925, 0.0)
RAY_COUNT = 4
REFLECTED_LENGTH = 9000.0
OUTPUT_PATH = os.path.join(
    unreal.Paths.project_saved_dir(),
    "SignalRayDemo",
    "single_source_rooftop_reflection.json",
)


def vec(v):
    return [round(float(v.x), 3), round(float(v.y), 3), round(float(v.z), 3)]


def length(v):
    return math.sqrt(v.x * v.x + v.y * v.y + v.z * v.z)


def normalized(v):
    value = length(v)
    if value <= 0.0001:
        return unreal.Vector(1.0, 0.0, 0.0)
    return unreal.Vector(v.x / value, v.y / value, v.z / value)


def distance(a, b):
    return length(a - b)


def reflect(direction, normal):
    direction = normalized(direction)
    normal = normalized(normal)
    dot = direction.x * normal.x + direction.y * normal.y + direction.z * normal.z
    return normalized(direction - normal * (2.0 * dot))


def keep_loaded(actor):
    actor.set_editor_property("is_spatially_loaded", False)
    return actor


def cleanup_generated_actors():
    for actor in list(unreal.EditorLevelLibrary.get_all_level_actors()):
        try:
            if actor.get_actor_label().startswith("SIG_"):
                unreal.EditorLevelLibrary.destroy_actor(actor)
        except Exception:
            pass


def collect_cesium_collision_components():
    components = []
    for actor in unreal.EditorLevelLibrary.get_all_level_actors():
        if actor.get_class().get_name() != "Cesium3DTileset":
            continue
        for component in actor.get_components_by_class(unreal.PrimitiveComponent):
            if (
                component.get_class().get_name() == "CesiumGltfPrimitiveComponent"
                and component.is_visible()
                and component.is_query_collision_enabled()
            ):
                components.append(component)
    if not components:
        raise RuntimeError("No visible Cesium collision components are ready")
    return components


def trace_components(components, start, end):
    """Return the nearest complex-mesh Cesium hit, or None."""
    best = None
    best_distance = None
    for component in components:
        result = component.line_trace_component(start, end, True, False, False)
        if not result:
            continue
        point, normal, _bone_name, _hit_result = result
        hit_distance = distance(start, point)
        if best is None or hit_distance < best_distance:
            best = {
                "point": point,
                "normal": normalized(normal),
                "component": component,
            }
            best_distance = hit_distance
    return best


def probe_rooftops(components):
    rooftops = []
    for ix in range(-2, 11):
        for iy in range(-5, 6):
            x = CENTER.x + ix * GRID_STEP
            y = CENTER.y + iy * GRID_STEP
            start = unreal.Vector(x, y, CENTER.z + 160000.0)
            end = unreal.Vector(x, y, CENTER.z - 180000.0)
            hit = trace_components(components, start, end)
            if (
                not hit
                or hit["normal"].z < ROOFTOP_NORMAL_Z_MIN
                or hit["point"].z < MIN_BUILDING_ROOFTOP_Z
                or hit["point"].y > URBAN_Y_MAX
            ):
                continue
            if any(distance(hit["point"], existing["point"]) < 3000.0 for existing in rooftops):
                continue
            rooftops.append(hit)
    if len(rooftops) < 2:
        raise RuntimeError("Fewer than two real Cesium rooftop hits were found")
    return rooftops


def spawn_sphere(location, material, label, scale):
    actor = keep_loaded(unreal.EditorLevelLibrary.spawn_actor_from_class(unreal.StaticMeshActor, location))
    actor.set_actor_label(label)
    actor.static_mesh_component.set_static_mesh(unreal.EditorAssetLibrary.load_asset("/Engine/BasicShapes/Sphere"))
    actor.static_mesh_component.set_material(0, material)
    actor.static_mesh_component.set_collision_enabled(unreal.CollisionEnabled.NO_COLLISION)
    actor.set_actor_scale3d(unreal.Vector(scale, scale, scale))
    return actor


def spawn_segment(start, end, material, label, radius):
    delta = end - start
    segment_length = max(length(delta), 1.0)
    midpoint = (start + end) * 0.5
    rotation = unreal.MathLibrary.make_rot_from_z(normalized(delta))
    actor = keep_loaded(unreal.EditorLevelLibrary.spawn_actor_from_class(unreal.StaticMeshActor, midpoint, rotation))
    actor.set_actor_label(label)
    actor.static_mesh_component.set_static_mesh(unreal.EditorAssetLibrary.load_asset("/Engine/BasicShapes/Cylinder"))
    actor.static_mesh_component.set_material(0, material)
    actor.static_mesh_component.set_collision_enabled(unreal.CollisionEnabled.NO_COLLISION)
    actor.set_actor_scale3d(unreal.Vector(radius, radius, segment_length / 100.0))
    return actor


def choose_real_reflections(components, source_roof, rooftops):
    source_start = source_roof["point"] + source_roof["normal"] * SOURCE_CLEARANCE
    candidates = sorted(
        rooftops,
        key=lambda item: distance(source_roof["point"], item["point"]),
        reverse=True,
    )
    reflections = []
    for candidate in candidates:
        if distance(source_roof["point"], candidate["point"]) < 14000.0:
            continue
        aim_direction = normalized(candidate["point"] - source_start)
        hit = trace_components(components, source_start, candidate["point"] + aim_direction * 3000.0)
        if not hit:
            continue
        if (
            hit["normal"].z < ROOFTOP_NORMAL_Z_MIN
            or hit["point"].z < MIN_BUILDING_ROOFTOP_Z
            or hit["point"].y > URBAN_Y_MAX
        ):
            continue
        if distance(source_start, hit["point"]) < 6000.0:
            continue
        if any(distance(hit["point"], item["point"]) < 5000.0 for item in reflections):
            continue
        incoming = normalized(hit["point"] - source_start)
        outgoing = reflect(incoming, hit["normal"])
        # A real upward roof reflection must leave the roof rather than continue
        # through it. Reject questionable normals instead of fabricating a path.
        if outgoing.z <= 0.05:
            continue
        hit["incoming"] = incoming
        hit["outgoing"] = outgoing
        reflections.append(hit)
        if len(reflections) >= RAY_COUNT:
            break
    if not reflections:
        raise RuntimeError("No real source-to-rooftop reflection path was found")
    return source_start, reflections


def set_close_validation_view(source, reflections):
    # Frame the first collision point closely enough to verify that the node is
    # physically embedded in the roof mesh, while keeping both ray legs visible.
    focus = reflections[0]["point"]
    camera_location = focus + unreal.Vector(-12000.0, -16000.0, 8000.0)
    rotation = unreal.MathLibrary.find_look_at_rotation(camera_location, focus)
    unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).set_level_viewport_camera_info(
        camera_location, rotation
    )


def main():
    components = collect_cesium_collision_components()
    rooftops = probe_rooftops(components)
    source_roof = min(
        rooftops,
        key=lambda item: math.hypot(
            item["point"].x - PREFERRED_SOURCE_XY.x,
            item["point"].y - PREFERRED_SOURCE_XY.y,
        ),
    )
    source, reflections = choose_real_reflections(components, source_roof, rooftops)

    cleanup_generated_actors()
    green = unreal.EditorAssetLibrary.load_asset("/Game/SignalRayDemo/Materials/MI_SignalRay_Green")
    yellow = unreal.EditorAssetLibrary.load_asset("/Game/SignalRayDemo/Materials/MI_SignalRay_Yellow") or green
    if not green:
        raise RuntimeError("Signal ray material is missing")

    spawn_sphere(source, green, "SIG_Prototype_Source_RealRooftop", 0.55)
    spawn_segment(
        source_roof["point"],
        source,
        green,
        "SIG_Prototype_Source_RooftopMast",
        0.28,
    )
    records = []
    for index, hit in enumerate(reflections):
        point = hit["point"]
        outgoing_end = point + hit["outgoing"] * REFLECTED_LENGTH
        spawn_segment(source, point, green, "SIG_Prototype_Incoming_{:02d}".format(index), 0.72)
        spawn_segment(point, outgoing_end, yellow, "SIG_Prototype_Reflected_{:02d}".format(index), 0.48)
        spawn_sphere(point, yellow, "SIG_Prototype_RealRoofHit_{:02d}".format(index), 0.18)
        records.append(
            {
                "reflection_point": vec(point),
                "impact_normal": vec(hit["normal"]),
                "normal_z": round(float(hit["normal"].z), 6),
                "component_class": hit["component"].get_class().get_name(),
                "incoming_start": vec(source),
                "reflected_end": vec(outgoing_end),
                "surface": "real_cesium_rooftop_collision",
            }
        )

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    result = {
        "mode": "single_source_real_rooftop_prototype",
        "source_count": 1,
        "source_rooftop_point": vec(source_roof["point"]),
        "source_world": vec(source),
        "cesium_query_component_count": len(components),
        "rooftop_probe_hit_count": len(rooftops),
        "real_reflection_count": len(records),
        "fallback_reflection_count": 0,
        "reflections": records,
    }
    with open(OUTPUT_PATH, "w", encoding="utf-8") as output:
        json.dump(result, output, ensure_ascii=False, indent=2)

    set_close_validation_view(source, reflections)
    unreal.EditorLoadingAndSavingUtils.save_dirty_packages(True, True)
    print(json.dumps(result, ensure_ascii=False, indent=2))


try:
    main()
except Exception:
    unreal.log_error(traceback.format_exc())
    raise
