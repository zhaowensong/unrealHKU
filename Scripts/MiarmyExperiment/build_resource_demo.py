"""Build a safe UE 5.7 demo from the Miarmy sample character assets.

The supplied Miarmy 2.1 DLL targets UE 5.2 and is intentionally disabled.
This script proves that the independent BattleWizard character and animation
assets can still be migrated into UE 5.7 without loading the incompatible DLL.
"""

import math

import unreal


MAP_PATH = "/Game/MiarmyExperiment/MiarmyResourceDemo"
MESH_PATH = "/Game/BattleWizardPolyart/Meshes/WizardSM"
ANIMATION_PATH = "/Game/BattleWizardPolyart/Animations/WalkForwardAnim"
GRID_SIZE = 6
GRID_SPACING = 260.0


def require_asset(path):
    asset = unreal.EditorAssetLibrary.load_asset(path)
    if not asset:
        raise RuntimeError("Required asset could not be loaded: {}".format(path))
    return asset


def set_skeletal_mesh(component, mesh):
    if hasattr(component, "set_skeletal_mesh_asset"):
        component.set_skeletal_mesh_asset(mesh)
    elif hasattr(component, "set_skeletal_mesh"):
        component.set_skeletal_mesh(mesh)
    else:
        component.set_editor_property("skeletal_mesh_asset", mesh)


def configure_animation(component, animation, play_rate):
    component.set_animation_mode(unreal.AnimationMode.ANIMATION_SINGLE_NODE)
    if hasattr(component, "play_animation"):
        component.play_animation(animation, True)
    else:
        component.set_animation(animation)
        component.set_looping(True)
        component.play(True)
    if hasattr(component, "set_play_rate"):
        component.set_play_rate(play_rate)


def main():
    mesh = require_asset(MESH_PATH)
    animation = require_asset(ANIMATION_PATH)

    level_subsystem = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
    if unreal.EditorAssetLibrary.does_asset_exist(MAP_PATH):
        if not level_subsystem.load_level(MAP_PATH):
            raise RuntimeError("Could not load demo level: {}".format(MAP_PATH))
    elif not level_subsystem.new_level(MAP_PATH):
        raise RuntimeError("Could not create demo level: {}".format(MAP_PATH))

    actor_subsystem = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    old_demo_actors = [
        actor
        for actor in actor_subsystem.get_all_level_actors()
        if actor.get_actor_label().startswith("MIG_")
    ]
    if old_demo_actors:
        actor_subsystem.destroy_actors(old_demo_actors)

    floor_mesh = require_asset("/Engine/BasicShapes/Plane.Plane")
    floor = unreal.EditorLevelLibrary.spawn_actor_from_class(
        unreal.StaticMeshActor, unreal.Vector(0.0, 0.0, -4.0)
    )
    floor.set_actor_label("MIG_Floor")
    floor.static_mesh_component.set_static_mesh(floor_mesh)
    floor.set_actor_scale3d(unreal.Vector(22.0, 22.0, 1.0))

    sun = unreal.EditorLevelLibrary.spawn_actor_from_class(
        unreal.DirectionalLight, unreal.Vector(0.0, 0.0, 900.0)
    )
    sun.set_actor_label("MIG_DirectionalLight")
    sun.set_actor_rotation(unreal.Rotator(-42.0, -35.0, 0.0), False)
    sun.light_component.set_editor_property("intensity", 7.0)

    skylight = unreal.EditorLevelLibrary.spawn_actor_from_class(
        unreal.SkyLight, unreal.Vector(0.0, 0.0, 700.0)
    )
    skylight.set_actor_label("MIG_SkyLight")
    skylight.light_component.set_editor_property("intensity", 1.2)

    start = -0.5 * (GRID_SIZE - 1) * GRID_SPACING
    created = []
    for row in range(GRID_SIZE):
        for column in range(GRID_SIZE):
            index = row * GRID_SIZE + column
            location = unreal.Vector(
                start + column * GRID_SPACING,
                start + row * GRID_SPACING,
                0.0,
            )
            actor = unreal.EditorLevelLibrary.spawn_actor_from_class(
                unreal.SkeletalMeshActor, location
            )
            actor.set_actor_label("MIG_Wizard_{:02d}".format(index))
            actor.set_actor_rotation(
                unreal.Rotator(0.0, float((index * 47) % 360), 0.0), False
            )
            scale = 0.88 + 0.04 * (index % 5)
            actor.set_actor_scale3d(unreal.Vector(scale, scale, scale))
            component = actor.skeletal_mesh_component
            set_skeletal_mesh(component, mesh)
            configure_animation(component, animation, 0.82 + 0.04 * (index % 6))
            created.append(actor)

    overview_location = unreal.Vector(-2500.0, -2500.0, 1900.0)
    overview_rotation = unreal.Rotator(-27.0, 45.0, 0.0)
    camera = unreal.EditorLevelLibrary.spawn_actor_from_class(
        unreal.CameraActor, overview_location
    )
    camera.set_actor_label("MIG_OverviewCamera")
    camera.set_actor_rotation(overview_rotation, False)

    if hasattr(level_subsystem, "set_level_viewport_camera_info"):
        level_subsystem.set_level_viewport_camera_info(
            overview_location, overview_rotation
        )
    else:
        unreal.EditorLevelLibrary.set_level_viewport_camera_info(
            overview_location, overview_rotation
        )

    if not unreal.EditorLevelLibrary.save_current_level():
        raise RuntimeError("Failed to save {}".format(MAP_PATH))

    unreal.log_warning(
        "MIARMY_RESOURCE_DEMO_OK map={} agents={} mesh={} animation={}".format(
            MAP_PATH, len(created), MESH_PATH, ANIMATION_PATH
        )
    )


main()
