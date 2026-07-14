import math

import unreal


ANCHORS = (
    unreal.Vector(-89800.0, 215800.0, 7000.0),
    unreal.Vector(-91000.0, 217000.0, 7000.0),
)
SEARCH_RADIUS = 18000.0
SEARCH_STEP = 600.0
MAX_STREET_Z = 4200.0
MIN_STREET_Z = 120.0
MIN_NORMAL_Z = 0.76
MAX_PATCH_SPREAD = 55.0


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
    start = unreal.Vector(x, y, 26000.0)
    end = unreal.Vector(x, y, -8000.0)
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


def validate_patch(components, center):
    # A pedestrian strip approximately 12 m long and 5 m wide.
    offsets = (
        (-600.0, -240.0), (-600.0, 0.0), (-600.0, 240.0),
        (0.0, -240.0), (0.0, 0.0), (0.0, 240.0),
        (600.0, -240.0), (600.0, 0.0), (600.0, 240.0),
    )
    points = []
    for dx, dy in offsets:
        hit = trace(components, center.x + dx, center.y + dy)
        if not hit:
            return None
        point, normal = hit
        if normal.z < MIN_NORMAL_Z or point.z > MAX_STREET_Z or point.z < MIN_STREET_Z:
            return None
        points.append(point)
    z_values = [p.z for p in points]
    spread = max(z_values) - min(z_values)
    if spread > MAX_PATCH_SPREAD:
        return None
    return sum(z_values) / len(z_values), spread


def main():
    components = collect_cesium_components()
    if not components:
        raise RuntimeError("No loaded Cesium collision components")

    candidates = []
    for anchor in ANCHORS:
        steps = int(SEARCH_RADIUS / SEARCH_STEP)
        for ix in range(-steps, steps + 1):
            for iy in range(-steps, steps + 1):
                if (ix * ix + iy * iy) > steps * steps:
                    continue
                x = anchor.x + ix * SEARCH_STEP
                y = anchor.y + iy * SEARCH_STEP
                hit = trace(components, x, y)
                if not hit:
                    continue
                point, normal = hit
                if point.z > MAX_STREET_Z or point.z < MIN_STREET_Z or normal.z < MIN_NORMAL_Z:
                    continue
                patch = validate_patch(components, point)
                if patch:
                    z, spread = patch
                    distance = math.hypot(x - anchor.x, y - anchor.y)
                    candidates.append((spread, distance, unreal.Vector(x, y, z)))
                    if len(candidates) >= 8:
                        break
            if len(candidates) >= 8:
                break
        if candidates:
            break

    if not candidates:
        raise RuntimeError("No loaded 12x5 m low, flat Cesium street patch found")

    candidates.sort(key=lambda item: (item[0], item[1]))
    for index, (spread, distance, point) in enumerate(candidates[:8]):
        unreal.log_warning(
            "HK_STREET_CANDIDATE index={} point=({:.2f},{:.2f},{:.2f}) spread={:.2f} distance={:.1f}".format(
                index, point.x, point.y, point.z, spread, distance
            )
        )

    selected = candidates[0][2]
    camera_location = selected + unreal.Vector(-1700.0, -2100.0, 1100.0)
    camera_rotation = unreal.MathLibrary.find_look_at_rotation(camera_location, selected)
    unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).set_level_viewport_camera_info(
        camera_location, camera_rotation
    )
    unreal.log_warning(
        "HK_STREET_SELECTED point=({:.2f},{:.2f},{:.2f}) components={}".format(
            selected.x, selected.y, selected.z, len(components)
        )
    )


main()
