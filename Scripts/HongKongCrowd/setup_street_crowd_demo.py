import unreal


MAP_PATH = "/Game/Maps/shanghai"
SPAWNER_LABEL = "HK_Street_Crowd_Spawner"
NAV_LABEL = "HK_Street_Crowd_NavBounds"
POPULATION = 30
STREET_CENTER = unreal.Vector(-97000.0, 222400.0, 394.76)
AREA_HALF_EXTENT = unreal.Vector2D(800.0, 300.0)


def collect_cesium_components():
    result = []
    for actor in unreal.EditorLevelLibrary.get_all_level_actors():
        if actor.get_class().get_name() != "Cesium3DTileset":
            continue
        for component in actor.get_components_by_class(unreal.PrimitiveComponent):
            if (
                component.get_class().get_name() == "CesiumGltfPrimitiveComponent"
                and component.is_visible()
                and component.is_query_collision_enabled()
            ):
                result.append(component)
    return result


def trace(components, x, y):
    start = unreal.Vector(x, y, STREET_CENTER.z + 2400.0)
    end = unreal.Vector(x, y, STREET_CENTER.z - 1800.0)
    best = None
    best_distance = None
    for component in components:
        hit = component.line_trace_component(start, end, True, False, False)
        if not hit:
            continue
        point, normal, _bone_name, _hit_result = hit
        distance = start.z - point.z
        if best is None or distance < best_distance:
            best = (point, normal)
            best_distance = distance
    return best


def validate_real_street(components):
    offsets = (
        (-AREA_HALF_EXTENT.x, -AREA_HALF_EXTENT.y),
        (-AREA_HALF_EXTENT.x, 0.0),
        (-AREA_HALF_EXTENT.x, AREA_HALF_EXTENT.y),
        (0.0, -AREA_HALF_EXTENT.y),
        (0.0, 0.0),
        (0.0, AREA_HALF_EXTENT.y),
        (AREA_HALF_EXTENT.x, -AREA_HALF_EXTENT.y),
        (AREA_HALF_EXTENT.x, 0.0),
        (AREA_HALF_EXTENT.x, AREA_HALF_EXTENT.y),
    )
    points = []
    for dx, dy in offsets:
        hit = trace(components, STREET_CENTER.x + dx, STREET_CENTER.y + dy)
        if not hit:
            raise RuntimeError("Selected Hong Kong street collision is not loaded")
        point, normal = hit
        if normal.z < 0.76:
            raise RuntimeError("Selected Hong Kong street patch is too steep")
        if not (120.0 <= point.z <= 900.0):
            raise RuntimeError("Selected surface is not the validated street elevation")
        points.append(point)

    z_values = [point.z for point in points]
    if max(z_values) - min(z_values) > 55.0:
        raise RuntimeError("Selected Hong Kong street patch is not continuous")
    return unreal.Vector(
        STREET_CENTER.x, STREET_CENTER.y, sum(z_values) / len(z_values)
    ), max(z_values) - min(z_values)


def delete_previous_demo():
    labels = {
        SPAWNER_LABEL,
        NAV_LABEL,
        "HK_Crowd_Spawner",
        "HK_Crowd_NavBounds",
    }
    for actor in list(unreal.EditorLevelLibrary.get_all_level_actors()):
        if actor.get_actor_label() in labels:
            unreal.EditorLevelLibrary.destroy_actor(actor)


def set_always_loaded(actor):
    if hasattr(actor, "set_is_spatially_loaded"):
        actor.set_is_spatially_loaded(False)
    else:
        actor.set_editor_property("is_spatially_loaded", False)


def schedule_navigation_rebuild(world):
    state = {"ticks": 0}

    def rebuild(_delta_seconds):
        state["ticks"] += 1
        if state["ticks"] < 30:
            return
        unreal.SystemLibrary.execute_console_command(world, "RebuildNavigation")
        unreal.EditorLevelLibrary.save_current_level()
        unreal.unregister_slate_post_tick_callback(state["handle"])
        unreal.log_warning("HK_STREET_CROWD_NAV_READY")

    state["handle"] = unreal.register_slate_post_tick_callback(rebuild)


def main():
    world = unreal.EditorLevelLibrary.get_editor_world()
    if not world or world.get_name() != "shanghai":
        unreal.EditorLoadingAndSavingUtils.load_map(MAP_PATH)
        world = unreal.EditorLevelLibrary.get_editor_world()

    components = collect_cesium_components()
    center, spread = validate_real_street(components)
    delete_previous_demo()

    spawner_class = getattr(unreal, "HongKongCrowdSpawner", None)
    if spawner_class is None:
        raise RuntimeError("HongKongCrowd plugin is not loaded")

    spawner = unreal.EditorLevelLibrary.spawn_actor_from_class(spawner_class, center)
    spawner.set_actor_label(SPAWNER_LABEL)
    spawner.set_editor_property("population_count", POPULATION)
    spawner.set_editor_property("area_half_extent", AREA_HALF_EXTENT)
    spawner.set_editor_property("ground_tolerance", max(35.0, spread + 8.0))
    set_always_loaded(spawner)

    nav = unreal.EditorLevelLibrary.spawn_actor_from_class(
        unreal.NavMeshBoundsVolume, center + unreal.Vector(0.0, 0.0, 220.0)
    )
    nav.set_actor_label(NAV_LABEL)
    nav.set_actor_scale3d(unreal.Vector(10.0, 5.0, 3.0))
    set_always_loaded(nav)

    camera_target = center + unreal.Vector(0.0, 0.0, 110.0)
    camera_location = center + unreal.Vector(-2100.0, -2400.0, 1150.0)
    camera_rotation = unreal.MathLibrary.find_look_at_rotation(
        camera_location, camera_target
    )
    unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).set_level_viewport_camera_info(
        camera_location, camera_rotation
    )

    if not unreal.EditorLevelLibrary.save_current_level():
        raise RuntimeError("Failed to save Hong Kong street crowd actors")

    schedule_navigation_rebuild(world)
    unreal.log_warning(
        "HK_STREET_CROWD_SETUP_OK population={} center=({:.2f},{:.2f},{:.2f}) spread={:.2f}".format(
            POPULATION, center.x, center.y, center.z, spread
        )
    )


main()
