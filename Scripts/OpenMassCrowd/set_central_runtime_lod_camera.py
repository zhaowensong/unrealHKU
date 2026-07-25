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
}


def pie_world():
    subsystem = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem)
    world = subsystem.get_game_world()
    world_path = world.get_path_name() if world is not None else ""
    if world is None or "UEDPIE_" not in world_path.upper():
        raise RuntimeError("an existing PIE world is required")
    return world


def central_evidence_target(world):
    actors = unreal.GameplayStatics.get_all_actors_of_class(world, unreal.Actor)
    spawners = [
        actor for actor in actors
        if actor.get_class().get_name() == "OpenMassCrowdSpawner"
    ]
    if len(spawners) != 1:
        raise RuntimeError(
            "expected one PIE OpenMassCrowdSpawner, found {}".format(len(spawners))
        )
    snapshot = json.loads(spawners[0].get_central_lod_evidence_snapshot())
    if not snapshot.get("valid"):
        raise RuntimeError(
            "stable Central evidence entity is unavailable: {}".format(snapshot)
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
        setattr(builtins, STATE_KEY, None)
        return {"status": "restored", "pawn": pawn.get_path_name()}

    target, target_snapshot = central_evidence_target(world)
    if not isinstance(state, dict):
        setattr(
            builtins,
            STATE_KEY,
            {
                "transform": pawn.get_actor_transform(),
                "control_rotation": controller.get_control_rotation(),
            },
        )
    offset = OFFSETS_CM[tier]
    location = target + unreal.Vector(*offset)
    rotation = look_at(location, target + unreal.Vector(0.0, 0.0, 90.0))
    pawn.set_actor_location(location, False, True)
    pawn.set_actor_rotation(rotation, True)
    controller.set_control_rotation(rotation)
    return {
        "status": "positioned",
        "tier": tier,
        "pawn": pawn.get_path_name(),
        "stable_target": target_snapshot,
        "location": [float(location.x), float(location.y), float(location.z)],
        "target": [float(target.x), float(target.y), float(target.z)],
        "distance_cm": round(
            math.sqrt(sum(float(value) ** 2 for value in offset)), 3
        ),
        "map_or_mass_entities_modified": False,
    }


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--tier", choices=("high", "low", "vat", "restore"), required=True)
arguments, _unknown = parser.parse_known_args(sys.argv[1:])
report = run(arguments.tier)
unreal.log_warning(
    "OPEN_MASS_CROWD_CENTRAL_LOD_CAMERA="
    + json.dumps(report, ensure_ascii=False, sort_keys=True)
)
