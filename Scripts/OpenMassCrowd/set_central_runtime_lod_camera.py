"""Move only the PIE viewer to a live City Sample crowd representation.

This helper never moves Mass entities or saves the map.  It uses the spawner's
stable Central evidence entity instead of an arbitrary VAT part, stores the
original PIE pawn transform in ``builtins``, and places the viewer inside the
configured High/Low/VAT distance band.  ``--tier restore`` restores the viewer
before PIE is stopped.
"""

from __future__ import annotations

import argparse
import builtins
import json
import math
import sys

import unreal


STATE_KEY = "_hk_central_runtime_lod_camera_state"
OFFSETS_CM = {
    # The gameplay viewport, not the editor viewport, owns HighResShot during
    # PIE.  Steep in-band views avoid Hong Kong photogrammetry foliage that is
    # visually opaque but has no query collision.
    # A lateral offset is essential: a camera directly above a valid pavement
    # point can still sit inside an overhang or dense photogrammetry shell.
    "high": (1200.0, 1200.0, 600.0),
    "low": (2400.0, 2400.0, 1200.0),
    "vat": (4200.0, 4200.0, 2400.0),
    # The far tier uses radial line-of-sight search below instead of this
    # fixed offset.  Keep the tuple as the requested horizontal/vertical
    # distance so the returned report still has an explicit visual gate.
    "far": (8000.0, 0.0, 1500.0),
}
FAR_REFERENCE_TARGET = unreal.Vector(-150248.0, 239745.0, 340.0)
FAR_REFERENCE_CAMERA = unreal.Vector(-142247.953, 239744.872, 1792.408)


def pie_world():
    subsystem = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem)
    world = subsystem.get_game_world()
    world_path = world.get_path_name() if world is not None else ""
    if world is None or "UEDPIE_" not in world_path.upper():
        raise RuntimeError("an existing PIE world is required")
    return world


def central_evidence_target(world, tier):
    actors = unreal.GameplayStatics.get_all_actors_of_class(world, unreal.Actor)
    spawners = [
        actor for actor in actors
        if actor.get_class().get_name() == "OpenMassCrowdSpawner"
    ]
    if len(spawners) != 1:
        raise RuntimeError(
            "expected one PIE OpenMassCrowdSpawner, found {}".format(len(spawners))
        )
    if tier == "far":
        candidates = []
        for stable_index in range(100):
            candidate = json.loads(
                spawners[0].get_central_vat_animation_evidence_snapshot_for_stable_index(
                    stable_index
                )
            )
            if not candidate.get("valid") or not candidate.get("animation_active"):
                continue
            point = candidate["location"]
            distance_squared = sum(
                (
                    float(point[key])
                    - float(getattr(FAR_REFERENCE_TARGET, key))
                )
                ** 2
                for key in ("x", "y", "z")
            )
            candidates.append((distance_squared, candidate))
        if not candidates:
            raise RuntimeError("no active VAT pedestrian is available")
        candidates.sort(key=lambda item: item[0])
        snapshot = candidates[0][1]
    else:
        snapshot = json.loads(spawners[0].get_central_lod_evidence_snapshot())
    if not snapshot.get("valid"):
        raise RuntimeError(
            "Central evidence entity is unavailable: {}".format(snapshot)
        )
    point = snapshot["location"]
    return unreal.Vector(point["x"], point["y"], point["z"]), snapshot


def look_at(start, target):
    # Unreal's pitch convention is not the mathematical atan2 convention used
    # by the previous implementation. Let the engine create the rotator so an
    # above-target camera actually looks down at the pedestrian instead of up
    # into the underside of the Cesium photogrammetry mesh.
    return unreal.MathLibrary.find_look_at_rotation(start, target)


def run(tier):
    world = pie_world()
    controller = unreal.GameplayStatics.get_player_controller(world, 0)
    if controller is None:
        raise RuntimeError("PIE has no player controller")
    pawn = unreal.GameplayStatics.get_player_pawn(world, 0)
    if pawn is None:
        raise RuntimeError("PIE player controller has no pawn")

    state = getattr(builtins, STATE_KEY, None)
    if tier == "restore":
        if not isinstance(state, dict):
            raise RuntimeError("no saved PIE viewer transform to restore")
        pawn.set_actor_transform(state["transform"], False, True)
        controller.set_control_rotation(state["control_rotation"])
        unreal.SystemLibrary.execute_console_command(
            world, "fov {}".format(float(state.get("fov", 90.0)))
        )
        setattr(builtins, STATE_KEY, None)
        return {"status": "restored", "pawn": pawn.get_path_name()}

    target, target_snapshot = central_evidence_target(world, tier)
    if not isinstance(state, dict):
        setattr(
            builtins,
            STATE_KEY,
            {
                "transform": pawn.get_actor_transform(),
                "control_rotation": controller.get_control_rotation(),
                "fov": float(controller.player_camera_manager.get_fov_angle()),
            },
        )
        state = getattr(builtins, STATE_KEY)
    state["target_stable_index"] = int(target_snapshot["stable_index"])
    state["target_person_id"] = str(target_snapshot["person_id"])
    state["target_snapshot"] = target_snapshot
    offset = OFFSETS_CM[tier]
    line_of_sight = None
    if tier == "far":
        # Cesium's visible photogrammetry and query collision do not match at
        # every tree/building shard. Use the calibrated Central road viewpoint
        # that was visually verified against the live tileset.
        location = FAR_REFERENCE_CAMERA
        line_of_sight = {
            "visually_calibrated": True,
            "reference_target": [
                float(FAR_REFERENCE_TARGET.x),
                float(FAR_REFERENCE_TARGET.y),
                float(FAR_REFERENCE_TARGET.z),
            ],
        }
    else:
        location = target + unreal.Vector(*offset)
    look_target = FAR_REFERENCE_TARGET if tier == "far" else target
    rotation = look_at(location, look_target + unreal.Vector(0.0, 0.0, 90.0))
    pawn.set_actor_location(location, False, True)
    pawn.set_actor_rotation(rotation, True)
    controller.set_control_rotation(rotation)
    if tier == "far":
        # Keep the physical 81 m distance while using a telephoto
        # lens, so leg/arm pose changes remain inspectable in a 1600x900 proof.
        unreal.SystemLibrary.execute_console_command(world, "fov 35")
    return {
        "status": "positioned",
        "tier": tier,
        "pawn": pawn.get_path_name(),
        "stable_target": target_snapshot,
        "location": [float(location.x), float(location.y), float(location.z)],
        "target": [float(target.x), float(target.y), float(target.z)],
        "distance_cm": round(
            math.sqrt(
                (float(location.x) - float(target.x)) ** 2
                + (float(location.y) - float(target.y)) ** 2
                + (float(location.z) - float(target.z)) ** 2
            ),
            3,
        ),
        "line_of_sight": line_of_sight,
        "map_or_mass_entities_modified": False,
    }


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument(
    "--tier", choices=("high", "low", "vat", "far", "restore"), required=True
)
arguments, _unknown = parser.parse_known_args(sys.argv[1:])
report = run(arguments.tier)
unreal.log_warning(
    "OPEN_MASS_CROWD_CENTRAL_LOD_CAMERA="
    + json.dumps(report, ensure_ascii=False, sort_keys=True)
)
