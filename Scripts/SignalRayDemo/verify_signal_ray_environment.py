import json
import re
from collections import Counter

import unreal


actor_subsystem = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
actors = list(actor_subsystem.get_all_level_actors())
managers = [actor for actor in actors if actor.get_class().get_name() == "SignalRayManager"]
legacy_signal_actors = [actor for actor in actors if actor.get_actor_label().startswith("SIG_")]
base_station_tops = [actor for actor in actors if re.fullmatch(r"BS_\d{2}_top", actor.get_actor_label())]
actor_prefix_counts = Counter(
    re.split(r"[_\s-]", actor.get_actor_label(), maxsplit=1)[0] for actor in actors
)
cesium_actors = [actor for actor in actors if "cesium" in actor.get_class().get_name().lower()]
named_collision_candidates = [
    actor
    for actor in actors
    if re.search(r"building|bldg|facade|collision|proxy", actor.get_actor_label(), re.IGNORECASE)
]

result = {
    "manager_class_loaded": getattr(unreal, "SignalRayManager", None) is not None,
    "manager_actor_count": len(managers),
    "legacy_sig_actor_count": len(legacy_signal_actors),
    "loaded_actor_count": len(actors),
    "base_station_tops": [
        {
            "label": actor.get_actor_label(),
            "class": actor.get_class().get_name(),
            "location": [round(value, 3) for value in actor.get_actor_location().to_tuple()],
        }
        for actor in sorted(base_station_tops, key=lambda item: item.get_actor_label())
    ],
    "actor_prefix_counts": dict(actor_prefix_counts.most_common(30)),
    "cesium_actors": [
        {
            "label": actor.get_actor_label(),
            "class": actor.get_class().get_name(),
            "collision_attributes": {
                name: str(getattr(actor, name))
                for name in dir(actor)
                if "collision" in name.lower() and not callable(getattr(actor, name, None))
            },
        }
        for actor in sorted(cesium_actors, key=lambda item: item.get_actor_label())
    ],
    "named_collision_candidates": [
        {"label": actor.get_actor_label(), "class": actor.get_class().get_name()}
        for actor in sorted(named_collision_candidates, key=lambda item: item.get_actor_label())[:100]
    ],
    "material_assets": {
        name: bool(unreal.EditorAssetLibrary.does_asset_exist(path))
        for name, path in {
            "base": "/Game/SignalRayDemo/Materials/M_SignalRay",
            "high": "/Game/SignalRayDemo/Materials/MI_SignalRay_Green",
            "medium": "/Game/SignalRayDemo/Materials/MI_SignalRay_Yellow",
            "low": "/Game/SignalRayDemo/Materials/MI_SignalRay_Red",
        }.items()
    },
}

if managers:
    manager = managers[0]
    result["manager_label"] = manager.get_actor_label()
    result["ray_segment_count"] = manager.get_ray_segment_count()
    result["reflection_node_count"] = manager.get_reflection_node_count()
    proxy_component = manager.building_collision_proxies
    result["collision_proxy_count"] = manager.get_collision_proxy_count()
    result["collision_component"] = {
        "collision_enabled": str(proxy_component.get_collision_enabled()),
        "visible": bool(proxy_component.is_visible()),
        "trace_methods": [name for name in dir(proxy_component) if "trace" in name.lower()],
    }
    result["ray_renderers"] = {
        "high": {
            "instances": manager.high_strength_rays.get_instance_count(),
            "material": manager.high_strength_rays.get_material(0).get_path_name(),
        },
        "medium": {
            "instances": manager.medium_strength_rays.get_instance_count(),
            "material": manager.medium_strength_rays.get_material(0).get_path_name(),
        },
        "low": {
            "instances": manager.low_strength_rays.get_instance_count(),
            "material": manager.low_strength_rays.get_material(0).get_path_name(),
        },
    }

print(json.dumps(result, ensure_ascii=False, indent=2))
