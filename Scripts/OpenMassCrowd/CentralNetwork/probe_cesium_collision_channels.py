"""Probe the actual UE collision channel/object type of one loaded Cesium tile."""

import json
import unreal


def hit_value(hit, name, default=None):
    try:
        return hit.get_editor_property(name)
    except Exception:
        return getattr(hit, name, default)


components = []
for actor in unreal.EditorLevelLibrary.get_all_level_actors():
    if "Cesium3DTileset" not in actor.get_class().get_name():
        continue
    for component in actor.get_components_by_class(unreal.PrimitiveComponent):
        if component.get_class().get_name() != "CesiumGltfPrimitiveComponent":
            continue
        if component.is_visible() and component.is_query_collision_enabled():
            origin, extent, _radius = unreal.SystemLibrary.get_component_bounds(component)
            components.append((component, origin, extent))

if not components:
    raise RuntimeError("no visible query-enabled Cesium primitive")

component, origin, extent = min(
    components,
    key=lambda item: abs(item[1].x + 158504.0) + abs(item[1].y - 254182.0),
)
start = unreal.Vector(origin.x, origin.y, origin.z + extent.z + 2000.0)
end = unreal.Vector(origin.x, origin.y, origin.z - extent.z - 2000.0)
world = unreal.EditorLevelLibrary.get_editor_world()

object_type = component.get_collision_object_type()
converted_object_type = None
conversion_error = None
try:
    converted_object_type = unreal.EngineTypes.convert_to_object_type(object_type)
except Exception as error:
    conversion_error = str(error)

object_queries = []
for index in range(1, 7):
    value = getattr(unreal.ObjectTypeQuery, "OBJECT_TYPE_QUERY{}".format(index))
    hit = unreal.SystemLibrary.line_trace_single_for_objects(
        world,
        start,
        end,
        [value],
        False,
        [],
        unreal.DrawDebugTrace.NONE,
        False,
    )
    hit_component = hit_value(hit, "component") or hit_value(hit, "hit_component")
    object_queries.append(
        {
            "query": str(value),
            "blocking_hit": bool(hit_value(hit, "blocking_hit", False)),
            "component_class": (
                hit_component.get_class().get_name() if hit_component else None
            ),
            "component_path": (
                hit_component.get_path_name() if hit_component else None
            ),
        }
    )

trace_queries = []
for index in range(1, 3):
    value = getattr(unreal.TraceTypeQuery, "TRACE_TYPE_QUERY{}".format(index))
    hit = unreal.SystemLibrary.line_trace_single(
        world,
        start,
        end,
        value,
        True,
        [],
        unreal.DrawDebugTrace.NONE,
        False,
    )
    hit_component = hit_value(hit, "component") or hit_value(hit, "hit_component")
    trace_queries.append(
        {
            "query": str(value),
            "blocking_hit": bool(hit_value(hit, "blocking_hit", False)),
            "component_class": (
                hit_component.get_class().get_name() if hit_component else None
            ),
            "component_path": (
                hit_component.get_path_name() if hit_component else None
            ),
        }
    )

converted_hit = None
if converted_object_type is not None:
    hit = unreal.SystemLibrary.line_trace_single_for_objects(
        world,
        start,
        end,
        [converted_object_type],
        True,
        [],
        unreal.DrawDebugTrace.NONE,
        True,
    )
    converted_component = hit_value(hit, "component") or hit_value(
        hit, "hit_component"
    )
    converted_hit = {
        "blocking_hit": bool(hit_value(hit, "blocking_hit", False)),
        "component_class": (
            converted_component.get_class().get_name() if converted_component else None
        ),
        "component_path": (
            converted_component.get_path_name() if converted_component else None
        ),
    }

direct_hit = component.line_trace_component(start, end, True, False, False)
report = {
    "component_path": component.get_path_name(),
    "collision_enabled": str(component.get_collision_enabled()),
    "collision_object_type": str(object_type),
    "collision_profile_name": str(component.get_collision_profile_name()),
    "converted_object_type": str(converted_object_type),
    "conversion_error": conversion_error,
    "converted_hit": converted_hit,
    "direct_hit": bool(direct_hit),
    "origin": [origin.x, origin.y, origin.z],
    "extent": [extent.x, extent.y, extent.z],
    "object_queries": object_queries,
    "trace_queries": trace_queries,
}
print("CENTRAL_CESIUM_COLLISION_CHANNEL_PROBE=" + json.dumps(report, sort_keys=True))
