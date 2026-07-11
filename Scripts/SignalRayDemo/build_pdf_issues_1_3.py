"""Solve PDF issues 1-3 using only real Cesium rooftop collision.

Issue 1: reduce the green ray's emissive intensity and scene bloom.
Issue 2: distribute eight signal sources across real rooftop anchors.
Issue 3: every reflection point must be returned by a complex triangle trace
         against a CesiumGltfPrimitiveComponent. There is no fallback plane.

Run this in the Unreal Editor Python console after the Central tiles have
finished streaming. The script deliberately leaves the existing SIG_ scene
untouched unless it can build the complete, auditable result.
"""

import json
import math
import os
import traceback

import unreal


SOURCE_COUNT = 8
RAY_COUNT = 64
SOURCE_CLEARANCE = 3000.0
REFLECTED_LENGTH = 8500.0
ROOF_NORMAL_Z_MIN = 0.82
MIN_ROOF_Z = 6500.0
MIN_SOURCE_SEPARATION = 9000.0
MIN_RAY_LENGTH = 7000.0
MAX_RAY_LENGTH = 175000.0
ROOF_CLUSTER_DISTANCE = 1200.0

# Central / Admiralty urban area in this level's Unreal coordinates.
PROBE_X_MIN = -250000.0
PROBE_X_MAX = -90000.0
PROBE_Y_MIN = 205000.0
PROBE_Y_MAX = 275000.0
PROBE_STEP = 3000.0
PROBE_TOP_Z = 175000.0
PROBE_BOTTOM_Z = -80000.0

OUTPUT_PATH = os.path.join(
    unreal.Paths.project_saved_dir(),
    "SignalRayDemo",
    "pdf_issues_1_3_real_collision.json",
)


def log(message):
    unreal.log("[PDFIssues1-3] " + str(message))


def vec(value):
    return [round(float(value.x), 3), round(float(value.y), 3), round(float(value.z), 3)]


def length(value):
    return math.sqrt(value.x * value.x + value.y * value.y + value.z * value.z)


def distance(a, b):
    return length(a - b)


def normalized(value, fallback=None):
    size = length(value)
    if size <= 0.0001:
        return fallback or unreal.Vector(1.0, 0.0, 0.0)
    return unreal.Vector(value.x / size, value.y / size, value.z / size)


def dot(a, b):
    return a.x * b.x + a.y * b.y + a.z * b.z


def reflected(direction, normal):
    direction = normalized(direction)
    normal = normalized(normal, unreal.Vector(0.0, 0.0, 1.0))
    return normalized(direction - normal * (2.0 * dot(direction, normal)))


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
    """Return the nearest complex-triangle hit across visible Cesium tiles."""
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


def is_rooftop(hit):
    if not hit:
        return False
    point = hit["point"]
    return (
        hit["normal"].z >= ROOF_NORMAL_Z_MIN
        and point.z >= MIN_ROOF_Z
        and PROBE_X_MIN <= point.x <= PROBE_X_MAX
        and PROBE_Y_MIN <= point.y <= PROBE_Y_MAX
        and hit["component"].get_class().get_name() == "CesiumGltfPrimitiveComponent"
    )


def probe_rooftops(components):
    rooftops = []
    x = PROBE_X_MIN
    while x <= PROBE_X_MAX + 0.1:
        y = PROBE_Y_MIN
        while y <= PROBE_Y_MAX + 0.1:
            start = unreal.Vector(x, y, PROBE_TOP_Z)
            end = unreal.Vector(x, y, PROBE_BOTTOM_Z)
            hit = trace_components(components, start, end)
            if is_rooftop(hit) and not any(
                math.hypot(hit["point"].x - roof["point"].x, hit["point"].y - roof["point"].y)
                < ROOF_CLUSTER_DISTANCE
                for roof in rooftops
            ):
                rooftops.append(hit)
            y += PROBE_STEP
        x += PROBE_STEP
    if len(rooftops) < SOURCE_COUNT:
        raise RuntimeError(
            "Only {} distinct real rooftops are loaded; {} are required".format(
                len(rooftops), SOURCE_COUNT
            )
        )
    return rooftops


def choose_distributed_sources(rooftops):
    """Farthest-point selection prevents the eight sources clustering together."""
    first = max(rooftops, key=lambda item: item["point"].z)
    selected = [first]
    remaining = [item for item in rooftops if item is not first]
    while remaining and len(selected) < SOURCE_COUNT:
        candidate = max(
            remaining,
            key=lambda item: min(
                math.hypot(
                    item["point"].x - chosen["point"].x,
                    item["point"].y - chosen["point"].y,
                )
                for chosen in selected
            ),
        )
        separation = min(
            math.hypot(
                candidate["point"].x - chosen["point"].x,
                candidate["point"].y - chosen["point"].y,
            )
            for chosen in selected
        )
        if separation < MIN_SOURCE_SEPARATION:
            break
        selected.append(candidate)
        remaining.remove(candidate)
    if len(selected) != SOURCE_COUNT:
        raise RuntimeError(
            "Could not select {} rooftop sources with real spatial separation".format(SOURCE_COUNT)
        )
    return selected


def choose_paths(components, source_roofs, rooftops):
    candidates_by_source = {index: [] for index in range(len(source_roofs))}
    used_pairs = set()
    for source_index, source_roof in enumerate(source_roofs):
        source = source_roof["point"] + source_roof["normal"] * SOURCE_CLEARANCE
        ordered = sorted(
            rooftops,
            key=lambda item: distance(source_roof["point"], item["point"]),
            reverse=True,
        )
        for target in ordered:
            target_distance = distance(source, target["point"])
            if target_distance < MIN_RAY_LENGTH or target_distance > MAX_RAY_LENGTH:
                continue
            aim = normalized(target["point"] - source)
            hit = trace_components(components, source, target["point"] + aim * 2500.0)
            if not is_rooftop(hit):
                continue
            if distance(hit["point"], target["point"]) > 3500.0:
                continue
            key = (
                source_index,
                round(hit["point"].x / 1000.0),
                round(hit["point"].y / 1000.0),
            )
            if key in used_pairs:
                continue
            incoming = normalized(hit["point"] - source)
            outgoing = reflected(incoming, hit["normal"])
            if outgoing.z <= 0.03:
                continue
            used_pairs.add(key)
            candidates_by_source[source_index].append(
                {
                    "source_index": source_index,
                    "source": source,
                    "source_roof": source_roof,
                    "hit": hit,
                    "incoming": incoming,
                    "outgoing": outgoing,
                }
            )

    empty_sources = [
        source_index
        for source_index, source_paths in candidates_by_source.items()
        if not source_paths
    ]
    if empty_sources:
        raise RuntimeError(
            "Sources {} have no real rooftop path ({} rooftop probes)".format(
                empty_sources, len(rooftops)
            )
        )

    # Round-robin selection keeps the visual network distributed even when
    # central sources have more line-of-sight rooftops than edge sources.
    paths = []
    per_source = {index: 0 for index in range(len(source_roofs))}
    next_index = {index: 0 for index in range(len(source_roofs))}
    while len(paths) < RAY_COUNT:
        added = False
        for source_index in range(len(source_roofs)):
            source_paths = candidates_by_source[source_index]
            index = next_index[source_index]
            if index >= len(source_paths):
                continue
            paths.append(source_paths[index])
            next_index[source_index] += 1
            per_source[source_index] += 1
            added = True
            if len(paths) >= RAY_COUNT:
                break
        if not added:
            break

    total_candidates = sum(len(value) for value in candidates_by_source.values())
    if len(paths) != RAY_COUNT:
        raise RuntimeError(
            "Found {} unique real rooftop candidates; {} selected but {} are required. Per source: {} ({} rooftop probes)".format(
                total_candidates, len(paths), RAY_COUNT, per_source, len(rooftops)
            )
        )
    return paths, per_source


def configure_issue_1_materials():
    material = unreal.EditorAssetLibrary.load_asset(
        "/Game/SignalRayDemo/Materials/MI_SignalRay_Green"
    )
    if not material:
        raise RuntimeError("MI_SignalRay_Green is missing")
    editing = unreal.MaterialEditingLibrary
    editing.set_material_instance_vector_parameter_value(
        material, "Color", unreal.LinearColor(0.0, 0.72, 0.08, 1.0)
    )
    editing.set_material_instance_scalar_parameter_value(material, "GlowIntensity", 8.0)
    editing.set_material_instance_scalar_parameter_value(material, "Opacity", 0.78)
    editing.update_material_instance(material)
    unreal.EditorAssetLibrary.save_asset(
        "/Game/SignalRayDemo/Materials/MI_SignalRay_Green"
    )
    return {
        "Green": material,
        "Yellow": unreal.EditorAssetLibrary.load_asset(
            "/Game/SignalRayDemo/Materials/MI_SignalRay_Yellow"
        ) or material,
        "Source": unreal.EditorAssetLibrary.load_asset(
            "/Game/SignalRayDemo/Materials/MI_SignalRay_Source"
        ) or material,
    }


def cleanup_generated_actors():
    removed = 0
    for actor in list(unreal.EditorLevelLibrary.get_all_level_actors()):
        try:
            if actor.get_actor_label().startswith("SIG_"):
                unreal.EditorLevelLibrary.destroy_actor(actor)
                removed += 1
        except Exception:
            pass
    log("Removed {} old SIG_ actors after validation succeeded".format(removed))


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
    return actor


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
    return actor


def add_post_process():
    actor = keep_loaded(
        unreal.EditorLevelLibrary.spawn_actor_from_class(
            unreal.PostProcessVolume, unreal.Vector(0.0, 0.0, 0.0)
        )
    )
    actor.set_actor_label("SIG_PostProcess_PDF_Issues_1_3")
    actor.set_editor_property("unbound", True)
    settings = actor.get_editor_property("settings")
    for name, value in [
        ("override_bloom_intensity", True),
        ("bloom_intensity", 0.45),
        ("override_bloom_threshold", True),
        ("bloom_threshold", 1.2),
        ("override_auto_exposure_bias", True),
        ("auto_exposure_bias", -0.35),
    ]:
        try:
            settings.set_editor_property(name, value)
        except Exception:
            pass
    actor.set_editor_property("settings", settings)


def set_overview_camera(source_roofs):
    center = unreal.Vector(
        sum(item["point"].x for item in source_roofs) / len(source_roofs),
        sum(item["point"].y for item in source_roofs) / len(source_roofs),
        sum(item["point"].z for item in source_roofs) / len(source_roofs),
    )
    camera = center + unreal.Vector(-65000.0, -90000.0, 52000.0)
    rotation = unreal.MathLibrary.find_look_at_rotation(camera, center)
    unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).set_level_viewport_camera_info(
        camera, rotation
    )


def build_visuals(materials, source_roofs, paths):
    for index, roof in enumerate(source_roofs):
        source = roof["point"] + roof["normal"] * SOURCE_CLEARANCE
        spawn_segment(
            roof["point"], source, materials["Source"],
            "SIG_Source_{:02d}_Roof_Mast".format(index), 0.20,
        )
        spawn_sphere(
            source, materials["Source"], "SIG_Source_{:02d}_Real_Roof".format(index), 0.42
        )

    for index, path in enumerate(paths):
        point = path["hit"]["point"]
        outgoing_end = point + path["outgoing"] * REFLECTED_LENGTH
        spawn_segment(
            path["source"], point, materials["Green"],
            "SIG_Ray_{:03d}_Incoming_Green".format(index), 0.48,
        )
        spawn_segment(
            point, outgoing_end, materials["Yellow"],
            "SIG_Ray_{:03d}_Reflected_Yellow".format(index), 0.34,
        )
        # The node center is exactly the returned Cesium impact point.
        spawn_sphere(
            point, materials["Yellow"], "SIG_Ray_{:03d}_Real_Roof_Hit".format(index), 0.12
        )
    add_post_process()


def save_evidence(components, rooftops, source_roofs, paths, per_source):
    records = []
    for index, path in enumerate(paths):
        hit = path["hit"]
        records.append(
            {
                "ray_id": "ray_{:03d}".format(index),
                "source_id": "source_{:02d}".format(path["source_index"]),
                "source_world": vec(path["source"]),
                "reflection_point": vec(hit["point"]),
                "impact_normal": vec(hit["normal"]),
                "normal_z": round(float(hit["normal"].z), 6),
                "component_class": hit["component"].get_class().get_name(),
                "incoming_color": "Green",
                "incoming_strength": 1.0,
                "reflected_color": "Yellow",
                "reflected_strength": 0.7,
                "surface": "real_cesium_rooftop_collision",
            }
        )
    result = {
        "mode": "pdf_issues_1_3_real_rooftop_collision",
        "issue_1": {
            "green_color": [0.0, 0.72, 0.08, 1.0],
            "green_glow_intensity": 8.0,
            "green_opacity": 0.78,
            "bloom_intensity": 0.45,
        },
        "issue_2": {
            "source_count": len(source_roofs),
            "selection": "farthest_point_sampling_on_real_rooftops",
            "sources": [
                {
                    "source_id": "source_{:02d}".format(index),
                    "roof_point": vec(roof["point"]),
                    "source_world": vec(roof["point"] + roof["normal"] * SOURCE_CLEARANCE),
                    "component_class": roof["component"].get_class().get_name(),
                }
                for index, roof in enumerate(source_roofs)
            ],
        },
        "issue_3": {
            "ray_count": len(paths),
            "real_reflection_count": len(paths),
            "fallback_reflection_count": 0,
            "rays_per_source": per_source,
            "reflection_surface": "rooftop_only",
            "rays": records,
        },
        "diagnostics": {
            "cesium_query_component_count": len(components),
            "distinct_rooftop_probe_count": len(rooftops),
        },
    }
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as output:
        json.dump(result, output, ensure_ascii=False, indent=2)
    return result


def main():
    components = collect_cesium_components()
    rooftops = probe_rooftops(components)
    source_roofs = choose_distributed_sources(rooftops)
    paths, per_source = choose_paths(components, source_roofs, rooftops)

    # Do not remove the stable scene until all 64 real paths have passed.
    materials = configure_issue_1_materials()
    cleanup_generated_actors()
    build_visuals(materials, source_roofs, paths)
    evidence = save_evidence(components, rooftops, source_roofs, paths, per_source)
    set_overview_camera(source_roofs)
    unreal.EditorLoadingAndSavingUtils.save_dirty_packages(True, True)
    print(json.dumps(evidence, ensure_ascii=False, indent=2))


try:
    main()
except Exception:
    unreal.log_error(traceback.format_exc())
    raise
