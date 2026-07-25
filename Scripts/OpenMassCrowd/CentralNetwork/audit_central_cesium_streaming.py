"""Read-only Central Cesium streaming/collision diagnostics for UE editor."""

from __future__ import annotations

import json
from pathlib import Path

import unreal


def _project_root() -> Path:
    return Path(unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_dir()))


def _bounds(component):
    origin, extent, radius = unreal.SystemLibrary.get_component_bounds(component)
    return origin, extent, radius


def _overlaps_xy(origin, extent, minimum, maximum) -> bool:
    return not (
        origin.x + extent.x < minimum.x
        or origin.x - extent.x > maximum.x
        or origin.y + extent.y < minimum.y
        or origin.y - extent.y > maximum.y
    )


def _hit_point(hit):
    try:
        if bool(hit.blocking_hit):
            return hit.impact_point
    except Exception:
        pass
    return None


def main() -> None:
    root = _project_root()
    source = json.loads(
        (
            root
            / "Scripts/OpenMassCrowd/CentralNetwork/Data/central_pedestrian_source_unreal.json"
        ).read_text(encoding="utf-8")
    )
    world_bounds = source["world_bounds_cm"]
    minimum = unreal.Vector(*[float(value) for value in world_bounds["min"]])
    maximum = unreal.Vector(*[float(value) for value in world_bounds["max"]])
    center = (minimum + maximum) * 0.5

    all_components = []
    tileset_records = []
    for actor in unreal.EditorLevelLibrary.get_all_level_actors():
        if actor.get_class().get_name() != "Cesium3DTileset":
            continue
        properties = {}
        for property_name in (
            "suspend_update",
            "update_in_editor",
            "maximum_screen_space_error",
            "create_physics_meshes",
            "enable_frustum_culling",
            "enable_occlusion_culling",
            "maximum_cached_bytes",
            "loading_descendant_limit",
        ):
            try:
                properties[property_name] = str(
                    actor.get_editor_property(property_name)
                )
            except Exception:
                properties[property_name] = "<unavailable>"
        tileset_records.append(
            {
                "path": actor.get_path_name(),
                "hidden": actor.is_hidden_ed(),
                "properties": properties,
            }
        )
        for component in actor.get_components_by_class(unreal.PrimitiveComponent):
            if component.get_class().get_name() == "CesiumGltfPrimitiveComponent":
                all_components.append(component)

    overlapping = []
    visible_query = []
    visible_query_overlapping = []
    for component in all_components:
        origin, extent, radius = _bounds(component)
        if component.is_visible() and component.is_query_collision_enabled():
            visible_query.append(component)
            if _overlaps_xy(origin, extent, minimum, maximum):
                visible_query_overlapping.append((component, origin, extent, radius))
        if _overlaps_xy(origin, extent, minimum, maximum):
            overlapping.append((component, origin, extent, radius))

    visible_query_nearest = sorted(
        (
            (
                max(0.0, abs(origin.x - center.x) - extent.x) ** 2
                + max(0.0, abs(origin.y - center.y) - extent.y) ** 2,
                component,
                origin,
                extent,
            )
            for component in visible_query
            for origin, extent, _radius in [_bounds(component)]
        ),
        key=lambda item: item[0],
    )

    sample_point = None
    for feature in source["features"]:
        if feature.get("requires_manual_review"):
            continue
        if feature.get("points"):
            sample_point = unreal.Vector(
                *[
                    float(value)
                    for value in feature["points"][0]["unreal_position_cm"]
                ]
            )
            break
    if sample_point is None:
        sample_point = center
    trace_start = sample_point + unreal.Vector(0.0, 0.0, 100000.0)
    trace_end = sample_point - unreal.Vector(0.0, 0.0, 100000.0)
    world = unreal.EditorLevelLibrary.get_editor_world()
    world_hit = unreal.SystemLibrary.line_trace_single(
        world,
        trace_start,
        trace_end,
        unreal.TraceTypeQuery.TRACE_TYPE_QUERY1,
        False,
        [],
        unreal.DrawDebugTrace.NONE,
        True,
    )
    direct_hits = []
    for component, origin, extent, _radius in visible_query_overlapping:
        result = component.line_trace_component(
            trace_start, trace_end, True, False, False
        )
        if result:
            direct_hits.append(
                {
                    "component": component.get_path_name(),
                    "result_repr": str(result),
                    "origin": [origin.x, origin.y, origin.z],
                    "extent": [extent.x, extent.y, extent.z],
                }
            )

    camera_location, camera_rotation = unreal.get_editor_subsystem(
        unreal.UnrealEditorSubsystem
    ).get_level_viewport_camera_info()
    report = {
        "schema_version": 1,
        "world": world.get_name() if world else None,
        "central_bounds": {
            "min": [minimum.x, minimum.y, minimum.z],
            "max": [maximum.x, maximum.y, maximum.z],
            "center": [center.x, center.y, center.z],
        },
        "camera": {
            "location": [camera_location.x, camera_location.y, camera_location.z],
            "rotation": [camera_rotation.pitch, camera_rotation.yaw, camera_rotation.roll],
        },
        "cesium_component_count": len(all_components),
        "tilesets": tileset_records,
        "visible_query_component_count": len(visible_query),
        "central_xy_overlap_component_count": len(overlapping),
        "central_visible_query_overlap_component_count": len(
            visible_query_overlapping
        ),
        "visible_query_overlap_examples": [
            {
                "component": component.get_path_name(),
                "origin": [origin.x, origin.y, origin.z],
                "extent": [extent.x, extent.y, extent.z],
            }
            for component, origin, extent, _radius in visible_query_overlapping[:12]
        ],
        "nearest_visible_query_examples": [
            {
                "xy_distance_cm": distance_squared ** 0.5,
                "component": component.get_path_name(),
                "origin": [origin.x, origin.y, origin.z],
                "extent": [extent.x, extent.y, extent.z],
            }
            for distance_squared, component, origin, extent in visible_query_nearest[:8]
        ],
        "overlap_examples": [
            {
                "component": component.get_path_name(),
                "origin": [origin.x, origin.y, origin.z],
                "extent": [extent.x, extent.y, extent.z],
                "visible": component.is_visible(),
                "query_collision": component.is_query_collision_enabled(),
            }
            for component, origin, extent, _radius in overlapping[:12]
        ],
        "sample_point": [sample_point.x, sample_point.y, sample_point.z],
        "world_hit": str(world_hit),
        "world_hit_point": (
            [
                _hit_point(world_hit).x,
                _hit_point(world_hit).y,
                _hit_point(world_hit).z,
            ]
            if _hit_point(world_hit)
            else None
        ),
        "direct_hit_count": len(direct_hits),
        "direct_hit_examples": direct_hits[:5],
    }
    print("CENTRAL_CESIUM_STREAM_AUDIT=" + json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
