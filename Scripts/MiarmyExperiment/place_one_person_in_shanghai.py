"""Place one UE 5.7-compatible BattleWizard character in TelecomTwin/shanghai.

The supplied Miarmy 2.1 DLL remains disabled because it targets UE 5.2. This
script uses only the compatible skeletal mesh and animation sample assets.
It is idempotent: rerunning updates the same labeled actor instead of creating
duplicates.
"""

import unreal


MAP_PATH = "/Game/Maps/shanghai"
PERSON_LABEL = "MIG_TelecomTwin_OnePerson"
MESH_PATH = "/Game/BattleWizardPolyart/Meshes/WizardSM"
ANIMATION_PATH = "/Game/BattleWizardPolyart/Animations/Idle01Anim"

# A previously audited real Cesium rooftop collision point used by source_00.
ROOF_POINT = unreal.Vector(-214000.0, 259000.0, 28991.011)
# Keep the person beside the audited source position on the same real rooftop.
PERSON_LOCATION = unreal.Vector(
    ROOF_POINT.x + 320.0, ROOF_POINT.y, ROOF_POINT.z + 95.0
)
PERSON_ROTATION = unreal.Rotator(0.0, 135.0, 0.0)


def require_asset(path):
    asset = unreal.EditorAssetLibrary.load_asset(path)
    if not asset:
        raise RuntimeError("Required asset could not be loaded: {}".format(path))
    return asset


def set_mesh(component, mesh):
    if hasattr(component, "set_skeletal_mesh_asset"):
        component.set_skeletal_mesh_asset(mesh)
    elif hasattr(component, "set_skeletal_mesh"):
        component.set_skeletal_mesh(mesh)
    else:
        component.set_editor_property("skeletal_mesh_asset", mesh)


def main():
    level_subsystem = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
    actor_subsystem = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)

    if not level_subsystem.load_level(MAP_PATH):
        raise RuntimeError("Could not load TelecomTwin map: {}".format(MAP_PATH))

    mesh = require_asset(MESH_PATH)
    animation = require_asset(ANIMATION_PATH)
    actors = actor_subsystem.get_all_level_actors()
    person = next(
        (actor for actor in actors if actor.get_actor_label() == PERSON_LABEL), None
    )

    if person is None:
        person = actor_subsystem.spawn_actor_from_class(
            unreal.SkeletalMeshActor, PERSON_LOCATION, PERSON_ROTATION
        )
        if not person:
            raise RuntimeError("Failed to spawn the TelecomTwin demo person")
        person.set_actor_label(PERSON_LABEL)
    else:
        person.set_actor_location(PERSON_LOCATION, False, False)
        person.set_actor_rotation(PERSON_ROTATION, False)

    person.set_actor_scale3d(unreal.Vector(1.25, 1.25, 1.25))
    component = person.skeletal_mesh_component
    set_mesh(component, mesh)
    component.set_animation_mode(unreal.AnimationMode.ANIMATION_SINGLE_NODE)
    component.play_animation(animation, True)

    # Aim the editor viewport at the character for immediate visual inspection.
    camera_location = unreal.Vector(
        PERSON_LOCATION.x - 2400.0,
        PERSON_LOCATION.y - 2400.0,
        PERSON_LOCATION.z + 1450.0,
    )
    camera_rotation = unreal.Rotator(-22.0, 45.0, 0.0)
    unreal.get_editor_subsystem(
        unreal.UnrealEditorSubsystem
    ).set_level_viewport_camera_info(camera_location, camera_rotation)

    if not level_subsystem.save_current_level():
        raise RuntimeError("Failed to save the shanghai level")
    unreal.EditorLoadingAndSavingUtils.save_dirty_packages(True, True)

    labels = [actor.get_actor_label() for actor in actor_subsystem.get_all_level_actors()]
    source_count = sum(
        label.startswith("SIG_Source_") and "Direct_Roof" in label for label in labels
    )
    color_counts = {
        color: sum(
            label.startswith("SIG_Ray_") and label.endswith("_" + color)
            for label in labels
        )
        for color in ("Green", "Yellow", "Orange", "Red")
    }
    person_count = sum(label == PERSON_LABEL for label in labels)
    unreal.log_warning(
        "MIARMY_SHANGHAI_PERSON_OK person={} sources={} green={} yellow={} orange={} red={} location=({}, {}, {})".format(
            person_count,
            source_count,
            color_counts["Green"],
            color_counts["Yellow"],
            color_counts["Orange"],
            color_counts["Red"],
            round(PERSON_LOCATION.x, 3),
            round(PERSON_LOCATION.y, 3),
            round(PERSON_LOCATION.z, 3),
        )
    )


main()
