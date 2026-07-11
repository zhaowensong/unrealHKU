"""Prototype PDF issue 4 with three real collision points per building.

For each of the eight distributed source buildings from the verified issues
1-3 build, this script keeps exactly three height samples:

* top: an upward-facing real roof hit;
* middle: a real facade hit nearest half of the measured facade span;
* bottom: the lowest real facade hit found on the same side of the building.

No point is created unless it comes from a complex triangle trace against a
CesiumGltfPrimitiveComponent.
"""

import json
import math
import os
import traceback

import unreal


BUILDING_ROOF_POINTS = [
    (-214000.0, 259000.0, 28991.011),
    (-91000.0, 217000.0, 6580.300),
    (-142000.0, 271000.0, 13818.197),
    (-94000.0, 265000.0, 11309.526),
    (-175000.0, 235000.0, 10697.424),
    (-184000.0, 274000.0, 10539.794),
    (-145000.0, 244000.0, 12459.329),
    (-208000.0, 235000.0, 8918.253),
]

ROOF_NORMAL_Z_MIN = 0.78
FACADE_NORMAL_Z_MAX = 0.72
RADIAL_DIRECTION_COUNT = 16
TRACE_OUTSIDE_RADIUS = 18000.0
TRACE_INSIDE_DEPTH = 4000.0
MAX_FACADE_XY_FROM_ROOF = 13500.0
HEIGHT_SCAN_STEP = 750.0
MIN_SCAN_Z = -2500.0
MIN_FACADE_SPAN = 1800.0
MAX_ADJACENT_SAMPLE_XY_JUMP = 1800.0
MIN_ADJACENT_NORMAL_DOT = 0.25
MAX_ROOF_TO_FIRST_FACADE_Z = 3500.0

OUTPUT_PATH = os.path.join(
    unreal.Paths.project_saved_dir(),
    "SignalRayDemo",
    "pdf_issue_4_three_height_points.json",
)


def vec(value):
    return [round(float(value.x), 3), round(float(value.y), 3), round(float(value.z), 3)]


def length(value):
    return math.sqrt(value.x * value.x + value.y * value.y + value.z * value.z)


def distance(a, b):
    return length(a - b)


def normalized(value):
    size = length(value)
    if size <= 0.0001:
        return unreal.Vector(1.0, 0.0, 0.0)
    return unreal.Vector(value.x / size, value.y / size, value.z / size)


def keep_loaded(actor):
    actor.set_editor_property("is_spatially_loaded", False)
    return actor


def collect_cesium_components():
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
    nearest = None
    nearest_distance = None
    for component in components:
        result = component.line_trace_component(start, end, True, False, False)
        if not result:
            continue
        point, normal, _bone_name, _hit_result = result
        hit_distance = distance(start, point)
        if nearest is None or hit_distance < nearest_distance:
            nearest = {
                "point": point,
                "normal": normalized(normal),
                "component": component,
            }
            nearest_distance = hit_distance
    return nearest


def horizontal_distance(a, b):
    return math.hypot(a.x - b.x, a.y - b.y)


def validate_roof_hit(components, expected):
    start = expected + unreal.Vector(0.0, 0.0, 6000.0)
    end = expected - unreal.Vector(0.0, 0.0, 2500.0)
    hit = trace_components(components, start, end)
    if (
        not hit
        or hit["normal"].z < ROOF_NORMAL_Z_MIN
        or horizontal_distance(hit["point"], expected) > 800.0
        or abs(hit["point"].z - expected.z) > 2500.0
    ):
        raise RuntimeError("Could not revalidate real roof point {}".format(vec(expected)))
    return hit


def facade_hits_for_direction(components, roof_hit, direction):
    roof = roof_hit["point"]
    samples = []
    z = roof.z - 900.0
    while z >= MIN_SCAN_Z:
        center = unreal.Vector(roof.x, roof.y, z)
        start = center + direction * TRACE_OUTSIDE_RADIUS
        end = center - direction * TRACE_INSIDE_DEPTH
        hit = trace_components(components, start, end)
        if (
            hit
            and abs(hit["normal"].z) <= FACADE_NORMAL_Z_MAX
            and horizontal_distance(hit["point"], roof) <= MAX_FACADE_XY_FROM_ROOF
            and abs(hit["point"].z - z) <= 300.0
            and hit["normal"].x * direction.x + hit["normal"].y * direction.y >= 0.20
        ):
            samples.append(hit)
        z -= HEIGHT_SCAN_STEP
    return samples


def choose_three_points(components, roof_hit):
    directional_samples = []
    for index in range(RADIAL_DIRECTION_COUNT):
        angle = (2.0 * math.pi * index) / RADIAL_DIRECTION_COUNT
        direction = unreal.Vector(math.cos(angle), math.sin(angle), 0.0)
        hits = facade_hits_for_direction(components, roof_hit, direction)
        hits = sorted(hits, key=lambda item: item["point"].z, reverse=True)
        chains = []
        current = []
        for hit in hits:
            if not current:
                current = [hit]
                continue
            previous = current[-1]
            z_gap = previous["point"].z - hit["point"].z
            normal_dot = (
                previous["normal"].x * hit["normal"].x
                + previous["normal"].y * hit["normal"].y
                + previous["normal"].z * hit["normal"].z
            )
            if (
                z_gap <= HEIGHT_SCAN_STEP * 2.1
                and horizontal_distance(previous["point"], hit["point"])
                <= MAX_ADJACENT_SAMPLE_XY_JUMP
                and normal_dot >= MIN_ADJACENT_NORMAL_DOT
            ):
                current.append(hit)
            else:
                chains.append(current)
                current = [hit]
        if current:
            chains.append(current)

        for chain in chains:
            if len(chain) < 3:
                continue
            top_z = chain[0]["point"].z
            bottom_z = chain[-1]["point"].z
            span = top_z - bottom_z
            if (
                span < MIN_FACADE_SPAN
                or roof_hit["point"].z - top_z > MAX_ROOF_TO_FIRST_FACADE_Z
            ):
                continue
            directional_samples.append(
                {
                    "direction_index": index,
                    "direction": direction,
                    "hits": chain,
                    "span": span,
                }
            )

    if not directional_samples:
        raise RuntimeError("No continuous real facade span was found below roof {}".format(vec(roof_hit["point"])))

    # Prefer the side with the largest measured vertical span, then the most
    # samples. Middle and bottom stay on this one facade side.
    best = max(directional_samples, key=lambda item: (item["span"], len(item["hits"])))
    bottom = min(best["hits"], key=lambda item: item["point"].z)
    middle_target_z = (roof_hit["point"].z + bottom["point"].z) * 0.5
    middle = min(best["hits"], key=lambda item: abs(item["point"].z - middle_target_z))
    return {
        "top": roof_hit,
        "middle": middle,
        "bottom": bottom,
        "direction_index": best["direction_index"],
        "facade_sample_count": len(best["hits"]),
        "measured_height": roof_hit["point"].z - bottom["point"].z,
    }


def cleanup_height_actors():
    for actor in list(unreal.EditorLevelLibrary.get_all_level_actors()):
        try:
            if actor.get_actor_label().startswith("SIG_Height_"):
                unreal.EditorLevelLibrary.destroy_actor(actor)
        except Exception:
            pass


def spawn_sphere(location, material, label, scale):
    actor = keep_loaded(
        unreal.EditorLevelLibrary.spawn_actor_from_class(unreal.StaticMeshActor, location)
    )
    actor.set_actor_label(label)
    component = actor.static_mesh_component
    component.set_static_mesh(unreal.EditorAssetLibrary.load_asset("/Engine/BasicShapes/Sphere"))
    component.set_material(0, material)
    component.set_collision_enabled(unreal.CollisionEnabled.NO_COLLISION)
    actor.set_actor_scale3d(unreal.Vector(scale, scale, scale))


def spawn_segment(start, end, material, label, radius):
    delta = end - start
    segment_length = max(length(delta), 1.0)
    midpoint = (start + end) * 0.5
    rotation = unreal.MathLibrary.make_rot_from_z(normalized(delta))
    actor = keep_loaded(
        unreal.EditorLevelLibrary.spawn_actor_from_class(
            unreal.StaticMeshActor, midpoint, rotation
        )
    )
    actor.set_actor_label(label)
    component = actor.static_mesh_component
    component.set_static_mesh(unreal.EditorAssetLibrary.load_asset("/Engine/BasicShapes/Cylinder"))
    component.set_material(0, material)
    component.set_collision_enabled(unreal.CollisionEnabled.NO_COLLISION)
    actor.set_actor_scale3d(unreal.Vector(radius, radius, segment_length / 100.0))


def build_visuals(buildings):
    green = unreal.EditorAssetLibrary.load_asset(
        "/Game/SignalRayDemo/Materials/MI_SignalRay_Green"
    )
    yellow = unreal.EditorAssetLibrary.load_asset(
        "/Game/SignalRayDemo/Materials/MI_SignalRay_Yellow"
    ) or green
    red = unreal.EditorAssetLibrary.load_asset(
        "/Game/SignalRayDemo/Materials/MI_SignalRay_Red"
    ) or yellow
    if not green:
        raise RuntimeError("Signal ray materials are missing")

    cleanup_height_actors()
    for index, building in enumerate(buildings):
        top = building["top"]["point"]
        middle = building["middle"]["point"]
        bottom = building["bottom"]["point"]
        prefix = "SIG_Height_Building_{:02d}_".format(index)
        spawn_sphere(top, green, prefix + "Top", 0.85)
        spawn_sphere(middle, yellow, prefix + "Middle", 0.85)
        spawn_sphere(bottom, red, prefix + "Bottom", 0.85)
        spawn_segment(top, middle, yellow, prefix + "TopToMiddle", 0.28)
        spawn_segment(middle, bottom, red, prefix + "MiddleToBottom", 0.28)


def set_validation_camera(buildings):
    points = [building["top"]["point"] for building in buildings]
    center = unreal.Vector(
        sum(point.x for point in points) / len(points),
        sum(point.y for point in points) / len(points),
        sum(point.z for point in points) / len(points),
    )
    camera = center + unreal.Vector(-52000.0, -72000.0, 36000.0)
    rotation = unreal.MathLibrary.find_look_at_rotation(camera, center)
    unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).set_level_viewport_camera_info(
        camera, rotation
    )


def save_evidence(components, buildings):
    records = []
    for index, building in enumerate(buildings):
        records.append(
            {
                "building_id": "building_{:02d}".format(index),
                "top": vec(building["top"]["point"]),
                "middle": vec(building["middle"]["point"]),
                "bottom": vec(building["bottom"]["point"]),
                "top_normal": vec(building["top"]["normal"]),
                "middle_normal": vec(building["middle"]["normal"]),
                "bottom_normal": vec(building["bottom"]["normal"]),
                "measured_height_cm": round(float(building["measured_height"]), 3),
                "facade_direction_index": building["direction_index"],
                "facade_sample_count": building["facade_sample_count"],
                "component_classes": [
                    building[name]["component"].get_class().get_name()
                    for name in ["top", "middle", "bottom"]
                ],
                "surface_source": "real_cesium_triangle_collision",
            }
        )
    result = {
        "mode": "pdf_issue_4_three_real_height_points_per_building",
        "building_count": len(buildings),
        "points_per_building": 3,
        "total_real_collision_points": len(buildings) * 3,
        "fallback_point_count": 0,
        "cesium_query_component_count": len(components),
        "buildings": records,
    }
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as output:
        json.dump(result, output, ensure_ascii=False, indent=2)
    return result


def main():
    components = collect_cesium_components()
    buildings = []
    for values in BUILDING_ROOF_POINTS:
        expected = unreal.Vector(*values)
        roof_hit = validate_roof_hit(components, expected)
        buildings.append(choose_three_points(components, roof_hit))

    build_visuals(buildings)
    evidence = save_evidence(components, buildings)
    set_validation_camera(buildings)
    unreal.EditorLoadingAndSavingUtils.save_dirty_packages(True, True)
    print(json.dumps(evidence, ensure_ascii=False, indent=2))


try:
    main()
except Exception:
    unreal.log_error(traceback.format_exc())
    raise
