import json
import math
import os
import random
import traceback

import unreal


PROJECT = "TelecomTwin"
AREA_NAME = "Hong Kong Island / Central"
SOURCE_LLH = [114.1588, 22.2795, 180.0]
RAY_COUNT = 64
MAX_REFLECTIONS = 3
ATTENUATION = 0.7
TRACE_DISTANCE = 250000.0
RANDOM_SEED = 7158

CONTENT_ROOT = "/Game/SignalRayDemo"
MAT_DIR = CONTENT_ROOT + "/Materials"
MESH_DIR = CONTENT_ROOT + "/Meshes"
BP_DIR = CONTENT_ROOT + "/Blueprints"

DATA_DIR = os.path.join(unreal.Paths.project_saved_dir(), "SignalRayDemo")
JSON_PATH = os.path.join(DATA_DIR, "signal_rays_mock.json")


def log(message):
    unreal.log("[SignalRayDemo] " + str(message))


def warn(message):
    unreal.log_warning("[SignalRayDemo] " + str(message))


def ensure_dirs():
    os.makedirs(DATA_DIR, exist_ok=True)
    for path in [CONTENT_ROOT, MAT_DIR, MESH_DIR, BP_DIR]:
        if not unreal.EditorAssetLibrary.does_directory_exist(path):
            unreal.EditorAssetLibrary.make_directory(path)


def vec_to_list(v):
    return [round(float(v.x), 3), round(float(v.y), 3), round(float(v.z), 3)]


def list_to_vec(values):
    return unreal.Vector(float(values[0]), float(values[1]), float(values[2]))


def v_len(v):
    return math.sqrt(v.x * v.x + v.y * v.y + v.z * v.z)


def v_norm(v, fallback=None):
    length = v_len(v)
    if length <= 0.0001:
        return fallback or unreal.Vector(1.0, 0.0, 0.0)
    return unreal.Vector(v.x / length, v.y / length, v.z / length)


def v_dot(a, b):
    return a.x * b.x + a.y * b.y + a.z * b.z


def v_reflect(direction, normal):
    n = v_norm(normal, unreal.Vector(0.0, 0.0, 1.0))
    d = v_norm(direction)
    return v_norm(d - n * (2.0 * v_dot(d, n)), d)


def hit_prop(hit, name, default=None):
    try:
        return getattr(hit, name)
    except Exception:
        try:
            return hit.get_editor_property(name)
        except Exception:
            return default


def find_actor_by_class_fragment(fragment):
    for actor in unreal.EditorLevelLibrary.get_all_level_actors():
        if fragment in actor.get_class().get_name():
            return actor
    return None


def cleanup_sig_actors():
    removed = 0
    for actor in list(unreal.EditorLevelLibrary.get_all_level_actors()):
        try:
            if actor.get_actor_label().startswith("SIG_"):
                unreal.EditorLevelLibrary.destroy_actor(actor)
                removed += 1
        except Exception:
            pass
    log("Cleaned {} existing SIG_ actors".format(removed))


def get_world_and_geo():
    world = unreal.EditorLevelLibrary.get_editor_world()
    if not world:
        raise RuntimeError("No editor world is loaded")
    geo = find_actor_by_class_fragment("CesiumGeoreference")
    if not geo:
        raise RuntimeError("CesiumGeoreference actor not found")
    return world, geo


def llh_to_unreal(geo, longitude, latitude, height):
    llh = [longitude, latitude, height]
    if hasattr(geo, "transform_longitude_latitude_height_position_to_unreal"):
        return geo.transform_longitude_latitude_height_position_to_unreal(llh)
    return geo.transform_longitude_latitude_height_to_unreal(llh)


def make_material_asset():
    ensure_dirs()
    tools = unreal.AssetToolsHelpers.get_asset_tools()
    material_path = MAT_DIR + "/M_SignalRay"
    material = unreal.EditorAssetLibrary.load_asset(material_path)
    if not material:
        material = tools.create_asset("M_SignalRay", MAT_DIR, unreal.Material, unreal.MaterialFactoryNew())

    material.set_editor_property("blend_mode", unreal.BlendMode.BLEND_ADDITIVE)
    material.set_editor_property("shading_model", unreal.MaterialShadingModel.MSM_UNLIT)
    material.set_editor_property("two_sided", True)
    material.set_editor_property("use_emissive_for_dynamic_area_lighting", True)

    mel = unreal.MaterialEditingLibrary
    mel.delete_all_material_expressions(material)
    color = mel.create_material_expression(material, unreal.MaterialExpressionVectorParameter, -600, -100)
    color.set_editor_property("parameter_name", "Color")
    color.set_editor_property("default_value", unreal.LinearColor(0.0, 1.0, 0.1, 1.0))

    glow = mel.create_material_expression(material, unreal.MaterialExpressionScalarParameter, -600, 120)
    glow.set_editor_property("parameter_name", "GlowIntensity")
    glow.set_editor_property("default_value", 50.0)

    multiply = mel.create_material_expression(material, unreal.MaterialExpressionMultiply, -260, 0)
    mel.connect_material_expressions(color, "", multiply, "A")
    mel.connect_material_expressions(glow, "", multiply, "B")
    mel.connect_material_property(multiply, "", unreal.MaterialProperty.MP_EMISSIVE_COLOR)

    opacity = mel.create_material_expression(material, unreal.MaterialExpressionScalarParameter, -260, 220)
    opacity.set_editor_property("parameter_name", "Opacity")
    opacity.set_editor_property("default_value", 0.92)
    mel.connect_material_property(opacity, "", unreal.MaterialProperty.MP_OPACITY)

    mel.layout_material_expressions(material)
    unreal.EditorAssetLibrary.save_asset(material_path)

    instances = {}
    specs = {
        "Green": (unreal.LinearColor(0.0, 1.0, 0.1, 1.0), 28.0),
        "Yellow": (unreal.LinearColor(1.0, 0.82, 0.0, 1.0), 24.0),
        "Red": (unreal.LinearColor(1.0, 0.05, 0.0, 1.0), 22.0),
        "Source": (unreal.LinearColor(1.0, 0.15, 0.04, 1.0), 42.0),
    }
    for name, (color_value, glow_value) in specs.items():
        asset_name = "MI_SignalRay_" + name
        path = MAT_DIR + "/" + asset_name
        instance = unreal.EditorAssetLibrary.load_asset(path)
        if not instance:
            instance = tools.create_asset(
                asset_name,
                MAT_DIR,
                unreal.MaterialInstanceConstant,
                unreal.MaterialInstanceConstantFactoryNew(),
            )
        mel.set_material_instance_parent(instance, material)
        mel.set_material_instance_vector_parameter_value(instance, "Color", color_value)
        mel.set_material_instance_scalar_parameter_value(instance, "GlowIntensity", glow_value)
        mel.set_material_instance_scalar_parameter_value(instance, "Opacity", 0.92)
        mel.update_material_instance(instance)
        unreal.EditorAssetLibrary.save_asset(path)
        instances[name] = instance

    return material, instances


def spawn_source(source_world, materials):
    sphere = unreal.EditorAssetLibrary.load_asset("/Engine/BasicShapes/Sphere")
    source = unreal.EditorLevelLibrary.spawn_actor_from_class(unreal.StaticMeshActor, source_world)
    source.set_actor_label("SIG_Source_Main")
    comp = source.static_mesh_component
    comp.set_static_mesh(sphere)
    comp.set_material(0, materials["Source"])
    comp.set_collision_enabled(unreal.CollisionEnabled.NO_COLLISION)
    source.set_actor_scale3d(unreal.Vector(2.6, 2.6, 2.6))

    light = unreal.EditorLevelLibrary.spawn_actor_from_class(unreal.PointLight, source_world)
    light.set_actor_label("SIG_Node_Source")
    light.point_light_component.set_light_color(unreal.LinearColor(1.0, 0.1, 0.04, 1.0))
    light.point_light_component.set_editor_property("intensity", 320000.0)
    light.point_light_component.set_editor_property("attenuation_radius", 26000.0)
    return source


def line_trace(world, start, end):
    channels = [unreal.TraceTypeQuery.TRACE_TYPE_QUERY1, unreal.TraceTypeQuery.TRACE_TYPE_QUERY2]
    for channel in channels:
        hit = unreal.SystemLibrary.line_trace_single(
            world,
            start,
            end,
            channel,
            False,
            [],
            unreal.DrawDebugTrace.NONE,
            True,
        )
        if hit_prop(hit, "blocking_hit", False):
            return hit
    return None


def direction_for_ray(index):
    golden_angle = math.pi * (3.0 - math.sqrt(5.0))
    yaw = index * golden_angle
    band = (index % 9) - 4
    pitch = math.radians(band * 2.2 + random.uniform(-1.4, 1.4))
    return v_norm(unreal.Vector(math.cos(yaw) * math.cos(pitch), math.sin(yaw) * math.cos(pitch), math.sin(pitch)))


def build_mock_facades(source):
    # Facade anchors are deliberately irregular. They form a street-level network
    # of building-side reflection points instead of a circular burst around one
    # center. Offsets are in UE centimeters, relative to the Central source.
    specs = [
        ((18000, -14000, -2600), (-0.80, 0.28, 0.08), 9000, 4200),
        ((28000, 18000, -1800), (-0.62, -0.55, 0.05), 11000, 5200),
        ((51000, -6000, -2300), (-0.20, 0.96, 0.04), 12500, 4800),
        ((62000, 26000, 3600), (-0.72, -0.36, 0.08), 9000, 7600),
        ((78000, -28000, -900), (-0.50, 0.82, 0.03), 14500, 5200),
        ((95000, 9000, 1800), (-0.91, -0.12, 0.04), 11500, 6500),
        ((112000, 36000, 4400), (-0.42, -0.78, 0.06), 12500, 7800),
        ((122000, -18000, 2200), (-0.68, 0.58, 0.05), 13500, 6200),
        ((41000, -39000, -2200), (0.18, 0.95, 0.04), 12500, 4800),
        ((72000, 47000, 900), (-0.30, -0.92, 0.06), 15000, 5200),
        ((142000, 19000, 6200), (-0.95, -0.02, 0.05), 10000, 8200),
        ((134000, -46000, 900), (-0.56, 0.76, 0.04), 15000, 5400),
    ]
    facades = []
    for offset, normal, width, height in specs:
        facades.append(
            {
                "center": source + unreal.Vector(float(offset[0]), float(offset[1]), float(offset[2])),
                "normal": v_norm(unreal.Vector(float(normal[0]), float(normal[1]), float(normal[2]))),
                "width": float(width),
                "height": float(height),
            }
        )
    return facades


def facade_point(facade, ray_index, bounce_index):
    normal = facade["normal"]
    horizontal = v_norm(unreal.Vector(-normal.y, normal.x, 0.0), unreal.Vector(1.0, 0.0, 0.0))
    vertical = unreal.Vector(0.0, 0.0, 1.0)

    # Low-discrepancy jitter gives thin bundles on the same facade without
    # collapsing every ray to a single bright point.
    u = ((ray_index * 37 + bounce_index * 11) % 101) / 100.0 - 0.5
    v = ((ray_index * 17 + bounce_index * 29) % 83) / 82.0 - 0.5
    micro = math.sin(ray_index * 1.73 + bounce_index * 2.19) * 0.18
    return facade["center"] + horizontal * (u * facade["width"]) + vertical * ((v + micro) * facade["height"]), normal


def route_for_ray(ray_index):
    routes = [
        [0, 2, 5, 10],
        [0, 3, 6, 11],
        [1, 2, 4, 7],
        [1, 9, 6, 10],
        [8, 2, 5, 7],
        [8, 4, 11, 6],
        [0, 4, 7, 10],
        [1, 3, 9, 6],
        [8, 5, 10, 7],
        [2, 3, 6, 11],
        [4, 5, 9, 10],
        [0, 8, 4, 11],
    ]
    return routes[ray_index % len(routes)]


def generate_rays(world, source_world):
    rays = []
    real_hit_count = 0
    fallback_segment_count = 0
    random.seed(RANDOM_SEED)
    facades = build_mock_facades(source_world)

    for ray_index in range(RAY_COUNT):
        current = unreal.Vector(source_world.x, source_world.y, source_world.z)
        route = route_for_ray(ray_index)
        strength = 1.0
        segments = []
        used_real_hit = False

        for bounce, facade_index in enumerate(route[: MAX_REFLECTIONS + 1]):
            target, mock_normal = facade_point(facades[facade_index], ray_index, bounce)
            direction = v_norm(target - current)
            end = current + direction * min(TRACE_DISTANCE, v_len(target - current))
            hit = line_trace(world, current, end)
            if hit:
                actor = hit_prop(hit, "hit_actor", None)
                impact = hit_prop(hit, "impact_point", None) or hit_prop(hit, "location", None)
                normal = hit_prop(hit, "impact_normal", None) or hit_prop(hit, "normal", None)
                if impact and normal:
                    segments.append(
                        {
                            "start": vec_to_list(current),
                            "end": vec_to_list(impact),
                            "strength": round(strength, 3),
                            "hit": True,
                            "hit_actor": actor.get_actor_label() if actor else None,
                            "hit_normal": vec_to_list(v_norm(normal)),
                            "source": "line_trace",
                        }
                    )
                    real_hit_count += 1
                    used_real_hit = True
                    reflected = v_reflect(direction, normal)
                    current = impact + reflected * 80.0
                    strength *= ATTENUATION
                    continue

            segments.append(
                {
                    "start": vec_to_list(current),
                    "end": vec_to_list(target),
                    "strength": round(strength, 3),
                    "hit": True,
                    "hit_actor": "Google_3D_Tiles_URL",
                    "hit_normal": vec_to_list(mock_normal),
                    "source": "mock_facade_reflection",
                    "mock_facade_id": facade_index,
                }
            )
            fallback_segment_count += 1
            reflected = v_reflect(direction, mock_normal)
            current = target + reflected * 100.0
            strength *= ATTENUATION

        rays.append({"id": "ray_{:03d}".format(ray_index), "segments": segments, "used_real_hit": used_real_hit})

    return rays, real_hit_count, fallback_segment_count


def save_ray_json(world, source_world, rays, real_hit_count, fallback_segment_count):
    data = {
        "metadata": {
            "project": PROJECT,
            "level": world.get_name(),
            "area": AREA_NAME,
            "source_actor": "SIG_Source_Main",
            "ray_count": RAY_COUNT,
            "max_reflections": MAX_REFLECTIONS,
            "attenuation": ATTENUATION,
            "trace_distance": TRACE_DISTANCE,
            "real_trace_hit_count": real_hit_count,
            "mock_reflection_segment_count": fallback_segment_count,
            "note": "Each segment attempts Unreal line trace first; mock_reflection segments are used when Cesium tile collision is unavailable.",
        },
        "source": {
            "world": vec_to_list(source_world),
            "lat_lon_height": [SOURCE_LLH[1], SOURCE_LLH[0], SOURCE_LLH[2]],
        },
        "rays": rays,
    }
    with open(JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    log("Saved ray data to {}".format(JSON_PATH))
    return data


def segment_bucket(strength):
    if strength > 0.7:
        return "Green"
    if strength >= 0.4:
        return "Yellow"
    return "Red"


def transform_for_segment(start, end, strength):
    midpoint = (start + end) * 0.5
    delta = end - start
    length = max(v_len(delta), 1.0)
    direction = v_norm(delta)
    rot = unreal.MathLibrary.make_rot_from_z(direction)
    radius = 0.35 + max(0.0, strength) * 0.25
    scale = unreal.Vector(radius, radius, length / 100.0)
    return unreal.Transform(midpoint, rot, scale)


def create_hism_actor(label, material):
    actor = unreal.EditorLevelLibrary.spawn_actor_from_class(unreal.Actor, unreal.Vector(0.0, 0.0, 0.0))
    actor.set_actor_label(label)
    comp = unreal.HierarchicalInstancedStaticMeshComponent(actor, label + "_Component")
    comp.set_static_mesh(unreal.EditorAssetLibrary.load_asset("/Engine/BasicShapes/Cylinder"))
    comp.set_material(0, material)
    comp.set_collision_enabled(unreal.CollisionEnabled.NO_COLLISION)
    try:
        comp.attach_to_component(
            actor.root_component,
            "",
            unreal.AttachmentRule.KEEP_RELATIVE,
            unreal.AttachmentRule.KEEP_RELATIVE,
            unreal.AttachmentRule.KEEP_RELATIVE,
            False,
        )
    except Exception:
        pass
    return actor, comp


def spawn_reflection_node(location, material, label, scale, color=None, create_light=True):
    sphere = unreal.EditorAssetLibrary.load_asset("/Engine/BasicShapes/Sphere")
    actor = unreal.EditorLevelLibrary.spawn_actor_from_class(unreal.StaticMeshActor, location)
    actor.set_actor_label(label)
    actor.static_mesh_component.set_static_mesh(sphere)
    actor.static_mesh_component.set_material(0, material)
    actor.static_mesh_component.set_collision_enabled(unreal.CollisionEnabled.NO_COLLISION)
    actor.set_actor_scale3d(unreal.Vector(scale, scale, scale))

    if create_light:
        light = unreal.EditorLevelLibrary.spawn_actor_from_class(unreal.PointLight, location)
        light.set_actor_label(label + "_Light")
        if color:
            light.point_light_component.set_light_color(color)
        light.point_light_component.set_editor_property("intensity", 12000.0 * max(0.5, scale))
        light.point_light_component.set_editor_property("attenuation_radius", 2500.0 + scale * 400.0)
    return actor


def spawn_ray_segment(start, end, material, label, strength, segment_index):
    cylinder = unreal.EditorAssetLibrary.load_asset("/Engine/BasicShapes/Cylinder")
    midpoint = (start + end) * 0.5
    delta = end - start
    length = max(v_len(delta), 1.0)
    direction = v_norm(delta)
    rotation = unreal.MathLibrary.make_rot_from_z(direction)

    actor = unreal.EditorLevelLibrary.spawn_actor_from_class(unreal.StaticMeshActor, midpoint, rotation)
    actor.set_actor_label(label)
    actor.static_mesh_component.set_static_mesh(cylinder)
    actor.static_mesh_component.set_material(0, material)
    actor.static_mesh_component.set_collision_enabled(unreal.CollisionEnabled.NO_COLLISION)

    # Engine basic cylinder is 100uu tall along Z; X/Y scale controls beam diameter.
    base_radius = 0.65 if segment_index == 0 else 0.42
    radius = base_radius + max(0.0, strength) * 0.35
    actor.set_actor_scale3d(unreal.Vector(radius, radius, length / 100.0))
    return actor


def build_visuals(data, materials):
    actors = {}
    comps = {}
    for bucket in ["Green", "Yellow", "Red"]:
        actor, comp = create_hism_actor("SIG_Ray_HISM_" + bucket, materials[bucket])
        actors[bucket] = actor
        comps[bucket] = comp

    node_count = 0
    segment_actor_count = 0
    bucket_colors = {
        "Green": unreal.LinearColor(0.0, 1.0, 0.1, 1.0),
        "Yellow": unreal.LinearColor(1.0, 0.85, 0.0, 1.0),
        "Red": unreal.LinearColor(1.0, 0.05, 0.0, 1.0),
    }
    for ray_index, ray in enumerate(data["rays"]):
        for segment_index, segment in enumerate(ray["segments"]):
            start = list_to_vec(segment["start"])
            end = list_to_vec(segment["end"])
            strength = float(segment["strength"])
            bucket = segment_bucket(strength)
            comps[bucket].add_instance_world_space(transform_for_segment(start, end, strength))
            spawn_ray_segment(
                start,
                end,
                materials[bucket],
                "SIG_RaySegment_{:03d}_{:02d}_{}".format(ray_index, segment_index, bucket),
                strength,
                segment_index,
            )
            segment_actor_count += 1
            if segment.get("hit"):
                spawn_reflection_node(
                    end,
                    materials[bucket],
                    "SIG_Node_Reflect_{:03d}".format(node_count),
                    0.08 + strength * 0.12,
                    bucket_colors[bucket],
                    False,
                )
                node_count += 1

    counts = {bucket: comps[bucket].get_instance_count() for bucket in comps}
    log("Built HISM ray instances: {}".format(counts))
    log("Built {} visible ray segment actors".format(segment_actor_count))
    log("Built {} reflection nodes".format(node_count))
    return counts, node_count, segment_actor_count


def add_post_process_and_camera(source_world):
    pp = unreal.EditorLevelLibrary.spawn_actor_from_class(unreal.PostProcessVolume, source_world)
    pp.set_actor_label("SIG_PostProcess")
    pp.set_editor_property("unbound", True)
    settings = pp.get_editor_property("settings")
    for prop, value in [
        ("override_bloom_intensity", True),
        ("bloom_intensity", 0.9),
        ("override_bloom_threshold", True),
        ("bloom_threshold", 1.2),
        ("override_auto_exposure_bias", True),
        ("auto_exposure_bias", -0.35),
    ]:
        try:
            settings.set_editor_property(prop, value)
        except Exception:
            pass
    pp.set_editor_property("settings", settings)

    camera_loc = source_world + unreal.Vector(26000.0, -112000.0, 52000.0)
    camera = unreal.EditorLevelLibrary.spawn_actor_from_class(unreal.CineCameraActor, camera_loc)
    camera.set_actor_label("SIG_Camera_Overview")
    look_rot = unreal.MathLibrary.find_look_at_rotation(camera_loc, source_world + unreal.Vector(78000.0, 6000.0, 4500.0))
    camera.set_actor_rotation(look_rot, False)
    try:
        camera.cine_camera_component.set_editor_property("current_focal_length", 24.0)
        camera.cine_camera_component.set_editor_property("current_aperture", 2.8)
    except Exception:
        pass

    unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).set_level_viewport_camera_info(camera_loc, look_rot)
    return pp, camera


def save_current_level():
    try:
        unreal.EditorLoadingAndSavingUtils.save_dirty_packages(True, True)
    except Exception as exc:
        warn("Could not save dirty packages automatically: {}".format(exc))


def main():
    ensure_dirs()
    world, geo = get_world_and_geo()
    cleanup_sig_actors()

    material, materials = make_material_asset()
    source_world = llh_to_unreal(geo, SOURCE_LLH[0], SOURCE_LLH[1], SOURCE_LLH[2])
    spawn_source(source_world, materials)

    rays, real_hit_count, fallback_segment_count = generate_rays(world, source_world)
    data = save_ray_json(world, source_world, rays, real_hit_count, fallback_segment_count)
    counts, node_count, segment_actor_count = build_visuals(data, materials)
    add_post_process_and_camera(source_world)
    save_current_level()

    result = {
        "json_path": JSON_PATH,
        "level": world.get_name(),
        "source_world": vec_to_list(source_world),
        "ray_count": len(rays),
        "segment_count": sum(len(ray["segments"]) for ray in rays),
        "real_trace_hit_count": real_hit_count,
        "mock_reflection_segment_count": fallback_segment_count,
        "hism_instance_counts": counts,
        "visible_ray_segment_actor_count": segment_actor_count,
        "reflection_node_count": node_count,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


try:
    main()
except Exception:
    unreal.log_error(traceback.format_exc())
    raise
