"""Read-only inventory for the TelecomTwin Hong Kong crowd prototype."""

import unreal


def read_property(obj, name):
    try:
        return obj.get_editor_property(name)
    except Exception as exc:
        return "unavailable:{}".format(exc)


actor_subsystem = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
editor_subsystem = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem)
actors = actor_subsystem.get_all_level_actors()

cesium = [actor for actor in actors if actor.get_class().get_name() == "Cesium3DTileset"]
nav = [actor for actor in actors if "NavMesh" in actor.get_class().get_name()]

unreal.log_warning(
    "HK_CROWD_INSPECT map={} actors={} cesium={} nav={}".format(
        unreal.EditorLevelLibrary.get_editor_world().get_name(),
        len(actors),
        len(cesium),
        len(nav),
    )
)

for actor in cesium:
    unreal.log_warning(
        "HK_CROWD_CESIUM label={} physics={} nav_collision={}".format(
            actor.get_actor_label(),
            read_property(actor, "create_physics_meshes"),
            read_property(actor, "create_nav_collision"),
        )
    )

for path in (
    "/Game/BattleWizardPolyart/Meshes/WizardSM",
    "/Game/BattleWizardPolyart/Animations/WalkForwardAnim",
    "/Game/BattleWizardPolyart/Animations/Idle01Anim",
):
    unreal.log_warning(
        "HK_CROWD_ASSET path={} loaded={}".format(
            path, bool(unreal.EditorAssetLibrary.load_asset(path))
        )
    )

camera_location, camera_rotation = editor_subsystem.get_level_viewport_camera_info()
unreal.log_warning(
    "HK_CROWD_CAMERA location={} rotation={}".format(camera_location, camera_rotation)
)
