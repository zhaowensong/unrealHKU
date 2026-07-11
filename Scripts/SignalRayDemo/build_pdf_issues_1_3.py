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
SEGMENTS_PER_PATH = 4
PATHS_PER_SOURCE = RAY_COUNT // SOURCE_COUNT
# Trace only: lift the ray origin enough to avoid immediately re-hitting the
# same photogrammetry roof triangle. Visual source/segment endpoints stay at
# the exact collision point, so this never creates an antenna or visible gap.
TRACE_SURFACE_EPSILON = 600.0
ROOF_NORMAL_Z_MIN = 0.82
MIN_ROOF_Z = 6500.0
MIN_SOURCE_SEPARATION = 9000.0
MIN_RAY_LENGTH = 4500.0
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
    """Find 64 complete four-hop rooftop paths with no fabricated point."""
    roof_index = {id(roof): index for index, roof in enumerate(rooftops)}
    source_indices = [roof_index[id(roof)] for roof in source_roofs]
    edge_cache = {}
    neighbor_cache = {}

    def trace_edge(start_hit, target_hit):
        start = start_hit["point"] + start_hit["normal"] * TRACE_SURFACE_EPSILON
        target = target_hit["point"]
        edge_length = distance(start, target)
        if edge_length < MIN_RAY_LENGTH or edge_length > MAX_RAY_LENGTH:
            return None
        direction = normalized(target - start)
        hit = trace_components(components, start, target + direction * 1800.0)
        if not is_rooftop(hit) or distance(hit["point"], target) > 2500.0:
            return None
        return hit

    def canonical_edge(start_index, target_index):
        key = (start_index, target_index)
        if key not in edge_cache:
            edge_cache[key] = trace_edge(rooftops[start_index], rooftops[target_index])
        return edge_cache[key]

    def neighbors(start_index):
        if start_index in neighbor_cache:
            return neighbor_cache[start_index]
        candidates = [index for index in range(len(rooftops)) if index != start_index]
        # A stable permutation spreads paths around the city instead of always
        # selecting the same nearest roofs.
        candidates.sort(
            key=lambda index: (
                (index * 37 + start_index * 17) % max(len(rooftops), 1),
                distance(rooftops[start_index]["point"], rooftops[index]["point"]),
            )
        )
        valid = []
        for target_index in candidates:
            if canonical_edge(start_index, target_index):
                valid.append(target_index)
                if len(valid) >= 20:
                    break
        neighbor_cache[start_index] = valid
        return valid

    def candidate_sequences(source_roof_index):
        sequences = []

        def visit(current_index, sequence, visited):
            if len(sequence) == SEGMENTS_PER_PATH + 1:
                sequences.append(list(sequence))
                return len(sequences) >= PATHS_PER_SOURCE * 3
            for target_index in neighbors(current_index):
                if target_index in visited:
                    continue
                sequence.append(target_index)
                visited.add(target_index)
                should_stop = visit(target_index, sequence, visited)
                visited.remove(target_index)
                sequence.pop()
                if should_stop:
                    return True
            return False

        visit(source_roof_index, [source_roof_index], {source_roof_index})
        return sequences

    paths = []
    per_source = {}
    for source_index, source_roof_index in enumerate(source_indices):
        completed = []
        for sequence in candidate_sequences(source_roof_index):
            current_hit = rooftops[sequence[0]]
            points = [current_hit]
            valid = True
            for target_index in sequence[1:]:
                hit = trace_edge(current_hit, rooftops[target_index])
                if not hit:
                    valid = False
                    break
                points.append(hit)
                current_hit = hit
            if not valid or len(points) != SEGMENTS_PER_PATH + 1:
                continue
            completed.append(
                {
                    "source_index": source_index,
                    "source_roof": source_roofs[source_index],
                    "points": points,
                    "roof_sequence": sequence,
                }
            )
            if len(completed) >= PATHS_PER_SOURCE:
                break
        if len(completed) != PATHS_PER_SOURCE:
            raise RuntimeError(
                "Source {} produced only {}/{} complete four-color real-rooftop paths".format(
                    source_index, len(completed), PATHS_PER_SOURCE
                )
            )
        paths.extend(completed)
        per_source[source_index] = len(completed)

    if len(paths) != RAY_COUNT:
        raise RuntimeError("Expected {} complete paths, got {}".format(RAY_COUNT, len(paths)))
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
    parent = unreal.EditorAssetLibrary.load_asset("/Game/SignalRayDemo/Materials/M_SignalRay")
    tools = unreal.AssetToolsHelpers.get_asset_tools()
    specs = {
        "Yellow": (unreal.LinearColor(1.0, 0.78, 0.0, 1.0), 12.0, 0.84),
        "Orange": (unreal.LinearColor(1.0, 0.24, 0.0, 1.0), 14.0, 0.86),
        "Red": (unreal.LinearColor(1.0, 0.02, 0.0, 1.0), 12.0, 0.84),
    }
    materials = {"Green": material}
    for name, (color, glow, opacity) in specs.items():
        path = "/Game/SignalRayDemo/Materials/MI_SignalRay_" + name
        instance = unreal.EditorAssetLibrary.load_asset(path)
        if not instance:
            instance = tools.create_asset(
                "MI_SignalRay_" + name,
                "/Game/SignalRayDemo/Materials",
                unreal.MaterialInstanceConstant,
                unreal.MaterialInstanceConstantFactoryNew(),
            )
        editing.set_material_instance_parent(instance, parent)
        editing.set_material_instance_vector_parameter_value(instance, "Color", color)
        editing.set_material_instance_scalar_parameter_value(instance, "GlowIntensity", glow)
        editing.set_material_instance_scalar_parameter_value(instance, "Opacity", opacity)
        editing.update_material_instance(instance)
        unreal.EditorAssetLibrary.save_asset(path)
        materials[name] = instance
    materials["Source"] = unreal.EditorAssetLibrary.load_asset(
        "/Game/SignalRayDemo/Materials/MI_SignalRay_Source"
    ) or materials["Orange"]
    return materials


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
    camera = center + unreal.Vector(-30000.0, -45000.0, 25000.0)
    rotation = unreal.MathLibrary.find_look_at_rotation(camera, center)
    unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).set_level_viewport_camera_info(
        camera, rotation
    )


def build_visuals(materials, source_roofs, paths):
    for index, roof in enumerate(source_roofs):
        # The source sits directly on the returned roof point. There is no mast,
        # antenna, clearance actor, or visual offset.
        spawn_sphere(
            roof["point"], materials["Source"],
            "SIG_Source_{:02d}_Direct_Roof".format(index), 0.62
        )

    colors = ["Green", "Yellow", "Orange", "Red"]
    for index, path in enumerate(paths):
        for segment_index, color in enumerate(colors):
            start = path["points"][segment_index]["point"]
            end = path["points"][segment_index + 1]["point"]
            spawn_segment(
                start, end, materials[color],
                "SIG_Ray_{:03d}_Segment_{:02d}_{}".format(index, segment_index, color),
                0.46 - segment_index * 0.045,
            )
            spawn_sphere(
                end, materials[color],
                "SIG_Ray_{:03d}_RoofHit_{:02d}_{}".format(index, segment_index, color),
                0.15,
            )
    add_post_process()


def save_evidence(components, rooftops, source_roofs, paths, per_source):
    colors = ["Green", "Yellow", "Orange", "Red"]
    strengths = [1.0, 0.7, 0.49, 0.343]
    records = []
    for index, path in enumerate(paths):
        segments = []
        for segment_index, color in enumerate(colors):
            start_hit = path["points"][segment_index]
            end_hit = path["points"][segment_index + 1]
            segments.append(
                {
                    "segment_index": segment_index,
                    "color": color,
                    "strength": strengths[segment_index],
                    "start": vec(start_hit["point"]),
                    "end": vec(end_hit["point"]),
                    "end_normal": vec(end_hit["normal"]),
                    "normal_z": round(float(end_hit["normal"].z), 6),
                    "component_class": end_hit["component"].get_class().get_name(),
                    "surface": "real_cesium_rooftop_collision",
                }
            )
        records.append(
            {
                "ray_id": "ray_{:03d}".format(index),
                "source_id": "source_{:02d}".format(path["source_index"]),
                "source_world": vec(path["points"][0]["point"]),
                "color_sequence": colors,
                "segments": segments,
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
                    "source_world": vec(roof["point"]),
                    "component_class": roof["component"].get_class().get_name(),
                    "has_mast_or_antenna": False,
                }
                for index, roof in enumerate(source_roofs)
            ],
        },
        "issue_3": {
            "ray_count": len(paths),
            "segments_per_path": SEGMENTS_PER_PATH,
            "required_color_sequence": colors,
            "real_rooftop_hit_count": len(paths) * SEGMENTS_PER_PATH,
            "fallback_reflection_count": 0,
            "mast_actor_count": 0,
            "antenna_actor_count": 0,
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
