"""Remove loaded legacy signal-ray actors from the current editor world.

Unloaded World Partition actor packages are removed by the repository cleanup
step after this script has destroyed and saved all currently loaded actors.
"""

import json

import unreal


LEGACY_PREFIXES = (
    "SIG_RaySegment_",
    "SIG_Node_Reflect_",
    "SIG_Source_",
    "SIG_Node_Source",
    "SIG_Ray_HISM_",
    "SIG_PostProcess",
    "SIG_Camera_",
)


def is_legacy_actor(actor):
    try:
        return actor.get_actor_label().startswith(LEGACY_PREFIXES)
    except Exception:
        return False


actor_subsystem = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
loaded_actors = actor_subsystem.get_all_level_actors()
legacy_actors = [actor for actor in loaded_actors if is_legacy_actor(actor)]
labels = sorted(actor.get_actor_label() for actor in legacy_actors)

destroyed = 0
if legacy_actors:
    destroyed = len(legacy_actors) if actor_subsystem.destroy_actors(legacy_actors) else 0

unreal.EditorLoadingAndSavingUtils.save_dirty_packages(True, True)

print(
    json.dumps(
        {
            "loaded_actor_count": len(loaded_actors),
            "legacy_actor_count": len(legacy_actors),
            "destroyed_actor_count": destroyed,
            "first_labels": labels[:20],
        },
        ensure_ascii=False,
        indent=2,
    )
)
