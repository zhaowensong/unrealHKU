"""Compare saved collision roof anchors against currently streamed Cesium LOD."""

from __future__ import annotations

import json
from pathlib import Path

import unreal


world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_game_world()
if world is None:
    raise RuntimeError("active PIE world required")
root = Path(
    unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_dir())
)
source_path = root / "Saved" / "SignalRayDemo" / "pdf_issues_1_3_real_collision.json"
payload = json.loads(source_path.read_text(encoding="utf-8"))
sources = payload["issue_2"]["sources"]
actors = unreal.GameplayStatics.get_all_actors_of_class(world, unreal.Actor)
tilesets = [actor for actor in actors if actor.get_class().get_name() == "Cesium3DTileset"]
components = []
for tileset in tilesets:
    components.extend(
        component
        for component in tileset.get_components_by_class(unreal.PrimitiveComponent)
        if component.get_class().get_name() == "CesiumGltfPrimitiveComponent"
        and component.is_visible()
        and component.is_query_collision_enabled()
    )

results = []
for source in sources:
    x, y, expected_z = [float(value) for value in source["roof_point"]]
    start = unreal.Vector(x, y, expected_z + 5000.0)
    end = unreal.Vector(x, y, expected_z - 5000.0)
    hits = []
    for component in components:
        result = component.line_trace_component(start, end, True, False, False)
        if not result:
            continue
        point, normal, _bone_name, _hit = result
        if float(normal.z) < 0.7:
            continue
        hits.append((abs(float(point.z) - expected_z), float(point.z)))
    if not hits:
        results.append(
            {"source_id": source["source_id"], "hit": False, "error_cm": None}
        )
        continue
    error_cm, actual_z = min(hits)
    results.append(
        {
            "source_id": source["source_id"],
            "hit": True,
            "x": x,
            "y": y,
            "expected_z": expected_z,
            "actual_z": actual_z,
            "error_cm": error_cm,
            "within_250_cm": error_cm <= 250.0,
        }
    )

results.sort(
    key=lambda item: (
        not bool(item.get("within_250_cm")),
        float(item["error_cm"]) if item["error_cm"] is not None else 1e30,
    )
)
print(
    "INVESTOR_ROOF_DIAGNOSTICS="
    + json.dumps(
        {
            "component_count": len(components),
            "results": results,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
)
