import math

import unreal


MAP_PATH = "/Game/Maps/shanghai"
SPAWNER_LABEL = "HK_Crowd_Spawner"
NAV_LABEL = "HK_Crowd_NavBounds"
POPULATION = 6

# Known collision-backed rooftop points captured by the existing telecom-ray
# validation.  The first point with a sufficiently large, flat loaded patch wins.
ROOF_CANDIDATES = (
    unreal.Vector(-91000.0, 217000.0, 6580.3),
    unreal.Vector(-175000.0, 235000.0, 10697.424),
    unreal.Vector(-94000.0, 265000.0, 11309.526),
    unreal.Vector(-142000.0, 271000.0, 13818.197),
)

AREA_HALF_X = 160.0
AREA_HALF_Y = 100.0
HEIGHT_SPREAD_LIMIT = 18.0
ROOF_NORMAL_Z_MIN = 0.72
SEARCH_RADIUS = 1800.0
SEARCH_STEP = 240.0


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
    return components


def trace_components(components, x, y, expected_z):
    start = unreal.Vector(x, y, expected_z + 1800.0)
    end = unreal.Vector(x, y, expected_z - 2200.0)
    nearest = None
    nearest_distance = None
    for component in components:
        result = component.line_trace_component(start, end, True, False, False)
        if not result:
            continue
        point, normal, _bone_name, _hit_result = result
        distance = math.sqrt(
            (start.x - point.x) ** 2
            + (start.y - point.y) ** 2
            + (start.z - point.z) ** 2
        )
        if nearest is None or distance < nearest_distance:
            nearest = (point, normal)
            nearest_distance = distance
    return nearest


def validate_flat_patch(components, candidate):
    offsets = (
        (0.0, 0.0),
        (-AREA_HALF_X * 0.78, -AREA_HALF_Y * 0.78),
        (-AREA_HALF_X * 0.78, 0.0),
        (-AREA_HALF_X * 0.78, AREA_HALF_Y * 0.78),
        (0.0, -AREA_HALF_Y * 0.78),
        (0.0, AREA_HALF_Y * 0.78),
        (AREA_HALF_X * 0.78, -AREA_HALF_Y * 0.78),
        (AREA_HALF_X * 0.78, 0.0),
        (AREA_HALF_X * 0.78, AREA_HALF_Y * 0.78),
    )
    points = []
    for dx, dy in offsets:
        hit = trace_components(components, candidate.x + dx, candidate.y + dy, candidate.z)
        if not hit:
            return None
        point, normal = hit
        if normal.z < ROOF_NORMAL_Z_MIN or abs(point.z - candidate.z) > 140.0:
            return None
        points.append(point)

    z_values = [point.z for point in points]
    spread = max(z_values) - min(z_values)
    if spread > HEIGHT_SPREAD_LIMIT:
        return None

    return sum(z_values) / len(z_values), spread


def search_near_candidate(components, anchor):
    offsets = [(0.0, 0.0)]
    radius = SEARCH_STEP
    while radius <= SEARCH_RADIUS + 0.1:
        steps = int(round(radius / SEARCH_STEP))
        for ix in range(-steps, steps + 1):
            for iy in range(-steps, steps + 1):
                if max(abs(ix), abs(iy)) != steps:
                    continue
                offsets.append((ix * SEARCH_STEP, iy * SEARCH_STEP))
        radius += SEARCH_STEP

    for dx, dy in offsets:
        center_hit = trace_components(
            components, anchor.x + dx, anchor.y + dy, anchor.z
        )
        if not center_hit:
            continue
        point, normal = center_hit
        if normal.z < ROOF_NORMAL_Z_MIN or abs(point.z - anchor.z) > 800.0:
            continue
        candidate = unreal.Vector(point.x, point.y, point.z)
        patch = validate_flat_patch(components, candidate)
        if patch:
            roof_z, spread = patch
            return unreal.Vector(candidate.x, candidate.y, roof_z), spread
    return None


def delete_previous_demo():
    for actor in list(unreal.EditorLevelLibrary.get_all_level_actors()):
        if actor.get_actor_label() in (SPAWNER_LABEL, NAV_LABEL):
            unreal.EditorLevelLibrary.destroy_actor(actor)


def get_spawner_class():
    cls = getattr(unreal, "HongKongCrowdSpawner", None)
    if cls is None:
        raise RuntimeError(
            "HongKongCrowd plugin class is unavailable; restart the editor after enabling the plugin"
        )
    return cls


def schedule_navigation_rebuild(world):
    state = {"ticks": 0}

    def rebuild_after_registration(_delta_seconds):
        state["ticks"] += 1
        if state["ticks"] < 15:
            return
        unreal.SystemLibrary.execute_console_command(world, "RebuildNavigation")
        unreal.EditorLevelLibrary.save_current_level()
        unreal.unregister_slate_post_tick_callback(state["handle"])
        unreal.log_warning("HK_CROWD_NAV_REBUILD_OK")

    state["handle"] = unreal.register_slate_post_tick_callback(
        rebuild_after_registration
    )


def main():
    world = unreal.EditorLevelLibrary.get_editor_world()
    if not world or world.get_name() != "shanghai":
        unreal.EditorLoadingAndSavingUtils.load_map(MAP_PATH)
        world = unreal.EditorLevelLibrary.get_editor_world()

    components = collect_cesium_components()
    if not components:
        raise RuntimeError("No loaded Cesium collision components; focus a candidate rooftop first")

    selected = None
    for candidate in ROOF_CANDIDATES:
        patch = search_near_candidate(components, candidate)
        if patch:
            selected = patch
            break

    if not selected:
        raise RuntimeError(
            "No candidate has a loaded 320x200 cm flat Cesium rooftop patch; move the viewport closer and retry"
        )

    center, spread = selected
    delete_previous_demo()

    spawner = unreal.EditorLevelLibrary.spawn_actor_from_class(get_spawner_class(), center)
    spawner.set_actor_label(SPAWNER_LABEL)
    spawner.set_editor_property("population_count", POPULATION)
    spawner.set_editor_property("area_half_extent", unreal.Vector2D(AREA_HALF_X, AREA_HALF_Y))
    spawner.set_editor_property("ground_tolerance", max(12.0, spread + 4.0))
    if hasattr(spawner, "set_is_spatially_loaded"):
        spawner.set_is_spatially_loaded(False)
    else:
        spawner.set_editor_property("is_spatially_loaded", False)

    nav = unreal.EditorLevelLibrary.spawn_actor_from_class(
        unreal.NavMeshBoundsVolume, center + unreal.Vector(0.0, 0.0, 220.0)
    )
    nav.set_actor_label(NAV_LABEL)
    nav.set_actor_scale3d(unreal.Vector(6.0, 4.2, 3.0))
    if hasattr(nav, "set_is_spatially_loaded"):
        nav.set_is_spatially_loaded(False)
    else:
        nav.set_editor_property("is_spatially_loaded", False)

    camera_location = center + unreal.Vector(-1350.0, -1550.0, 950.0)
    camera_rotation = unreal.MathLibrary.find_look_at_rotation(camera_location, center)
    unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).set_level_viewport_camera_info(
        camera_location, camera_rotation
    )

    if not unreal.EditorLevelLibrary.save_current_level():
        raise RuntimeError("Failed to save the Hong Kong crowd demo level")

    schedule_navigation_rebuild(world)

    bounds_origin, bounds_extent = nav.get_actor_bounds(False, False)
    unreal.log_warning(
        "HK_CROWD_SETUP_OK population={} center=({:.2f},{:.2f},{:.2f}) "
        "roof_spread={:.2f} nav_extent=({:.1f},{:.1f},{:.1f})".format(
            POPULATION,
            center.x,
            center.y,
            center.z,
            spread,
            bounds_extent.x,
            bounds_extent.y,
            bounds_extent.z,
        )
    )


main()
