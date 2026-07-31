"""Move the active PIE viewer to a clear view of the densest ground crowd."""

from __future__ import annotations

import json
import math

import unreal


world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_game_world()
if world is None or "UEDPIE_" not in world.get_path_name().upper():
    raise RuntimeError("active PIE world required")
spawners = unreal.GameplayStatics.get_all_actors_of_class(
    world, unreal.OpenMassCrowdSpawner
)
if len(spawners) != 1:
    raise RuntimeError("expected exactly one OpenMassCrowdSpawner")
spawner = spawners[0]

# This street-level view is retained from the collision-certified Central-300
# evidence. It provides a deterministic first camera when the current PIE
# camera is so far away that Mass has correctly placed every representation in
# Off LOD and there is not yet a visible proxy to cluster around.
fallback_camera = unreal.Vector(-169758.3608, 261509.5433, 545.6582)
fallback_target = unreal.Vector(-169667.5698, 262716.5051, 385.6582)

people = []
for stable_index in range(int(spawner.get_spawned_entity_count())):
    snapshot = json.loads(
        spawner.get_central_vat_animation_evidence_snapshot_for_stable_index(
            stable_index
        )
    )
    if not snapshot.get("valid"):
        continue
    location = snapshot["location"]
    point = unreal.Vector(
        float(location["x"]), float(location["y"]), float(location["z"])
    )
    if float(point.z) <= 2500.0:
        people.append((stable_index, point, snapshot))


def distance_squared_2d(first, second):
    return (float(first.x) - float(second.x)) ** 2 + (
        float(first.y) - float(second.y)
    ) ** 2


controller = unreal.GameplayStatics.get_player_controller(world, 0)
pawn = controller.get_controlled_pawn() or controller.get_spectator_pawn()
if controller is None or pawn is None:
    raise RuntimeError("PIE viewer is unavailable")

if people:
    radius_squared = 3000.0**2
    clusters = []
    for stable_index, anchor, snapshot in people:
        neighbors = [
            item
            for item in people
            if distance_squared_2d(anchor, item[1]) <= radius_squared
        ]
        moving = sum(
            float(item[2].get("speed_cm_s", 0.0)) >= 20.0
            for item in neighbors
        )
        clusters.append((len(neighbors), moving, stable_index, anchor, neighbors))
    clusters.sort(key=lambda item: (item[0], item[1]), reverse=True)
    _count, _moving, selected_index, _anchor, neighbors = clusters[0]

    count = float(len(neighbors))
    target = unreal.Vector(
        sum(float(item[1].x) for item in neighbors) / count,
        sum(float(item[1].y) for item in neighbors) / count,
        sum(float(item[1].z) for item in neighbors) / count + 90.0,
    )
    camera = None
    tested = 0
    for distance, height in (
        (2200.0, 650.0),
        (3000.0, 950.0),
        (3800.0, 1300.0),
    ):
        for angle_index in range(24):
            angle = math.radians(angle_index * 15.0)
            candidate = target + unreal.Vector(
                math.cos(angle) * distance,
                math.sin(angle) * distance,
                height,
            )
            tested += 1
            hit = unreal.SystemLibrary.line_trace_single(
                world,
                target,
                candidate,
                unreal.TraceTypeQuery.TRACE_TYPE_QUERY1,
                True,
                [pawn],
                unreal.DrawDebugTrace.NONE,
                True,
            )
            if not bool(getattr(hit, "blocking_hit", False)):
                camera = candidate
                break
        if camera is not None:
            break
    if camera is None:
        raise RuntimeError("no clear crowd camera found after {} tests".format(tested))
    used_fallback = False
else:
    camera = fallback_camera
    target = fallback_target
    selected_index = 0
    neighbors = []
    _moving = 0
    tested = 0
    used_fallback = True

rotation = unreal.MathLibrary.find_look_at_rotation(camera, target)
pawn.set_actor_location(camera, False, False)
pawn.set_actor_rotation(rotation, True)
controller.set_control_rotation(rotation)
unreal.get_editor_subsystem(
    unreal.UnrealEditorSubsystem
).set_level_viewport_camera_info(camera, rotation)
spawner.show_central_profile_by_stable_index(int(selected_index))
print(
    "INVESTOR_PEOPLE_CAMERA={}"
    .format(
        json.dumps(
            {
                "camera": {"x": camera.x, "y": camera.y, "z": camera.z},
                "target": {"x": target.x, "y": target.y, "z": target.z},
                "selected_stable_index": selected_index,
                "cluster_people": len(neighbors),
                "moving_people": _moving,
                "line_of_sight_tests": tested,
                "used_fallback": used_fallback,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
)
