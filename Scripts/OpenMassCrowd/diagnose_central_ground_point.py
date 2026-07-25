"""Read-only exact-XY collision diagnostics for a Central certified point."""

from __future__ import annotations

import argparse
import json

import unreal


def _vector(value: str) -> unreal.Vector:
    parts = [float(part.strip()) for part in value.split(",")]
    if len(parts) != 3:
        raise argparse.ArgumentTypeError("--xyz must be x,y,z")
    return unreal.Vector(*parts)


def _hit_property(hit, name: str, default=None):
    try:
        return getattr(hit, name)
    except Exception:
        pass
    try:
        return hit.get_editor_property(name)
    except Exception:
        return default


def _component_record(component) -> dict:
    if component is None:
        return {"path": None, "class": None, "owner": None}
    owner = component.get_owner()
    return {
        "path": component.get_path_name(),
        "class": component.get_class().get_name(),
        "owner": owner.get_path_name() if owner else None,
    }


def _hit_record(hit) -> dict | None:
    if not bool(_hit_property(hit, "blocking_hit", False)):
        return None
    point = _hit_property(hit, "impact_point") or _hit_property(hit, "location")
    normal = _hit_property(hit, "impact_normal") or _hit_property(hit, "normal")
    component = _hit_property(hit, "component") or _hit_property(
        hit, "hit_component"
    )
    return {
        **_component_record(component),
        "point": [float(point.x), float(point.y), float(point.z)] if point else None,
        "normal": (
            [float(normal.x), float(normal.y), float(normal.z)] if normal else None
        ),
    }


def _direct_hit_record(component, result) -> dict | None:
    if not result:
        return None
    point, normal, _bone_name, _hit_result = result
    return {
        **_component_record(component),
        "point": [float(point.x), float(point.y), float(point.z)],
        "normal": [float(normal.x), float(normal.y), float(normal.z)],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--xyz", type=_vector, required=True)
    parser.add_argument("--trace-height", type=float, default=1600.0)
    parser.add_argument("--trace-depth", type=float, default=2600.0)
    args = parser.parse_args()

    subsystem = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem)
    game_world = subsystem.get_game_world()
    world = game_world or subsystem.get_editor_world()
    if world is None:
        raise RuntimeError("no editor or PIE world")

    point = args.xyz
    start = point + unreal.Vector(0.0, 0.0, args.trace_height)
    end = point - unreal.Vector(0.0, 0.0, args.trace_depth)
    actors = (
        list(unreal.GameplayStatics.get_all_actors_of_class(world, unreal.Actor))
        if game_world
        else unreal.EditorLevelLibrary.get_all_level_actors()
    )
    spawners = [
        actor
        for actor in actors
        if actor.get_class().get_name() == "OpenMassCrowdSpawner"
    ]
    world_hit = unreal.SystemLibrary.line_trace_single(
        world,
        start,
        end,
        unreal.TraceTypeQuery.TRACE_TYPE_QUERY1,
        True,
        spawners,
        unreal.DrawDebugTrace.NONE,
        True,
    )

    direct_hits = []
    relevant_count = 0
    cesium_component_count = 0
    visible_query_component_count = 0
    nearest_components = []
    for actor in actors:
        for component in actor.get_components_by_class(unreal.PrimitiveComponent):
            if component.get_class().get_name() != "CesiumGltfPrimitiveComponent":
                continue
            cesium_component_count += 1
            if not component.is_visible() or not component.is_query_collision_enabled():
                continue
            visible_query_component_count += 1
            origin, extent, _radius = unreal.SystemLibrary.get_component_bounds(component)
            dx = max(0.0, abs(float(origin.x) - float(point.x)) - float(extent.x))
            dy = max(0.0, abs(float(origin.y) - float(point.y)) - float(extent.y))
            nearest_components.append(
                {
                    **_component_record(component),
                    "xy_distance_cm": (dx * dx + dy * dy) ** 0.5,
                    "origin": [float(origin.x), float(origin.y), float(origin.z)],
                    "extent": [float(extent.x), float(extent.y), float(extent.z)],
                }
            )
            if not (
                origin.x - extent.x <= point.x <= origin.x + extent.x
                and origin.y - extent.y <= point.y <= origin.y + extent.y
            ):
                continue
            relevant_count += 1
            try:
                result = component.line_trace_component(
                    start, end, True, False, False
                )
            except Exception as exc:
                direct_hits.append(
                    {**_component_record(component), "error": str(exc)}
                )
                continue
            record = _direct_hit_record(component, result)
            if record:
                direct_hits.append(record)

    direct_hits.sort(
        key=lambda hit: (
            (start.x - hit["point"][0]) ** 2
            + (start.y - hit["point"][1]) ** 2
            + (start.z - hit["point"][2]) ** 2
            if hit.get("point")
            else float("inf")
        )
    )
    nearest_components.sort(key=lambda item: item["xy_distance_cm"])
    report = {
        "schema_version": 1,
        "world": world.get_name(),
        "world_type": "PIE" if game_world else "editor",
        "query": [float(point.x), float(point.y), float(point.z)],
        "start": [float(start.x), float(start.y), float(start.z)],
        "end": [float(end.x), float(end.y), float(end.z)],
        "world_hit": _hit_record(world_hit),
        "cesium_component_count": cesium_component_count,
        "visible_query_component_count": visible_query_component_count,
        "relevant_cesium_component_count": relevant_count,
        "nearest_cesium_components": nearest_components[:8],
        "direct_cesium_hits": direct_hits,
        "tilesets": [
            {
                "path": tileset.get_path_name(),
                "load_progress": float(tileset.get_editor_property("load_progress")),
                "suspend_update": bool(
                    tileset.get_editor_property("suspend_update")
                ),
            }
            for tileset in unreal.GameplayStatics.get_all_actors_of_class(
                world, unreal.Cesium3DTileset
            )
        ],
    }
    print("CENTRAL_GROUND_POINT_DIAGNOSTIC=" + json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
