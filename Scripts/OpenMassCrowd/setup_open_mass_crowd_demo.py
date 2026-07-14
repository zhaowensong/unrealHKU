"""Place the UE 5.7 Mass/ZoneGraph crowd in the validated Hong Kong street.

This setup is intentionally isolated from the telecom ray actors. It replaces
only the previous crowd demo actors and validates the walking loop directly
against loaded Cesium collision before saving the level.
"""

import unreal


MAP_PATH = "/Game/Maps/shanghai"
SPAWNER_LABEL = "HK_OpenMass_Crowd_Spawner"
POPULATION = 30
STREET_CENTER = unreal.Vector(-97000.0, 222400.0, 394.76)
ROUTE_HALF_EXTENT = unreal.Vector2D(720.0, 250.0)
MAX_SETUP_TICKS = 1800

OLD_CROWD_LABELS = {
    "HK_OpenMass_Crowd_Spawner",
    "HK_Street_Crowd_Spawner",
    "HK_Street_Crowd_NavBounds",
    "HK_Crowd_Spawner",
    "HK_Crowd_NavBounds",
}

_state = {"ticks": 0, "handle": None}


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


def trace_cesium_ground(components, x, y):
    start = unreal.Vector(x, y, STREET_CENTER.z + 1600.0)
    end = unreal.Vector(x, y, STREET_CENTER.z - 2600.0)
    nearest = None
    nearest_distance = None
    for component in components:
        hit = component.line_trace_component(start, end, True, False, False)
        if not hit:
            continue
        point, normal, _bone_name, _hit_result = hit
        distance = start.z - point.z
        if nearest is None or distance < nearest_distance:
            nearest = (point, normal)
            nearest_distance = distance
    return nearest


def route_offsets():
    x = ROUTE_HALF_EXTENT.x
    y = ROUTE_HALF_EXTENT.y
    return (
        (-x, -y), (-0.5 * x, -y), (0.0, -y), (0.5 * x, -y),
        (x, -y), (x, 0.0), (x, y), (0.5 * x, y),
        (0.0, y), (-0.5 * x, y), (-x, y), (-x, 0.0),
    )


def validate_route(components):
    points = []
    for dx, dy in route_offsets():
        hit = trace_cesium_ground(
            components, STREET_CENTER.x + dx, STREET_CENTER.y + dy
        )
        if not hit:
            return None
        point, normal = hit
        if normal.z < 0.72 or abs(point.z - STREET_CENTER.z) > 90.0:
            return None
        points.append(point)

    heights = [point.z for point in points]
    spread = max(heights) - min(heights)
    if spread > 75.0:
        return None
    return unreal.Vector(
        STREET_CENTER.x,
        STREET_CENTER.y,
        sum(heights) / len(heights),
    ), spread


def delete_previous_crowd():
    for actor in list(unreal.EditorLevelLibrary.get_all_level_actors()):
        if actor.get_actor_label() in OLD_CROWD_LABELS:
            unreal.EditorLevelLibrary.destroy_actor(actor)


def set_always_loaded(actor):
    if hasattr(actor, "set_is_spatially_loaded"):
        actor.set_is_spatially_loaded(False)
    else:
        actor.set_editor_property("is_spatially_loaded", False)


def finish_callback():
    if _state["handle"] is not None:
        unreal.unregister_slate_post_tick_callback(_state["handle"])
        _state["handle"] = None


def attempt_setup(_delta_seconds):
    _state["ticks"] += 1
    if _state["ticks"] % 30 != 0:
        return

    components = collect_cesium_components()
    validated = validate_route(components) if components else None
    if validated is None:
        if _state["ticks"] >= MAX_SETUP_TICKS:
            finish_callback()
            unreal.log_error(
                "HK_OPEN_MASS_CROWD_SETUP_ABORT Cesium route collision was not ready"
            )
        elif _state["ticks"] % 300 == 0:
            unreal.log_warning(
                "HK_OPEN_MASS_CROWD_SETUP_WAIT ticks={} components={}".format(
                    _state["ticks"], len(components)
                )
            )
        return

    center, spread = validated
    delete_previous_crowd()

    spawner_class = getattr(unreal, "OpenMassCrowdSpawner", None)
    if spawner_class is None:
        finish_callback()
        raise RuntimeError("OpenMassCrowd plugin class is unavailable")

    spawner = unreal.EditorLevelLibrary.spawn_actor_from_class(spawner_class, center)
    spawner.set_actor_label(SPAWNER_LABEL)
    spawner.set_editor_property("population_count", POPULATION)
    spawner.set_editor_property("route_half_extent", ROUTE_HALF_EXTENT)
    spawner.set_editor_property("ground_tolerance", max(45.0, spread + 12.0))
    set_always_loaded(spawner)

    camera_target = center + unreal.Vector(0.0, 0.0, 100.0)
    camera_location = center + unreal.Vector(-2100.0, -2400.0, 1150.0)
    camera_rotation = unreal.MathLibrary.find_look_at_rotation(
        camera_location, camera_target
    )
    unreal.get_editor_subsystem(
        unreal.UnrealEditorSubsystem
    ).set_level_viewport_camera_info(camera_location, camera_rotation)

    if not unreal.EditorLevelLibrary.save_current_level():
        finish_callback()
        raise RuntimeError("Failed to save the OpenMassCrowd spawner")

    finish_callback()
    unreal.log_warning(
        "HK_OPEN_MASS_CROWD_SETUP_OK population={} route_points=12 "
        "cesium_components={} center=({:.2f},{:.2f},{:.2f}) spread={:.2f}".format(
            POPULATION,
            len(components),
            center.x,
            center.y,
            center.z,
            spread,
        )
    )


def main():
    world = unreal.EditorLevelLibrary.get_editor_world()
    if not world or world.get_name() != "shanghai":
        unreal.EditorLoadingAndSavingUtils.load_map(MAP_PATH)

    _state["handle"] = unreal.register_slate_post_tick_callback(attempt_setup)
    unreal.log_warning("HK_OPEN_MASS_CROWD_SETUP_STARTED")


main()
