"""Select a crowd mode for the next PIE session without saving the level.

This is the safe regression switch used after the Central-300 gate.  The on-disk
map remains the promoted Central configuration, so closing the editor always
abandons this temporary override.  Run only while PIE is stopped::

    python run_unreal_python_via_mcp.py --file \
      Scripts/OpenMassCrowd/set_open_mass_crowd_session_mode.py \
      --script-arg=local30
"""

from __future__ import annotations

import argparse
import json

import unreal


MAP_OBJECT_PATH = "/Game/Maps/shanghai.shanghai"
ALLOWED_MODES = ("local30", "central30", "central100", "central200", "central300")


def require_type(name: str):
    value = getattr(unreal, name, None)
    if value is None:
        raise RuntimeError("required reflected type is unavailable: {}".format(name))
    return value


def enum_value(type_name: str, value_name: str):
    enum_type = require_type(type_name)
    value = getattr(enum_type, value_name, None)
    if value is None:
        raise RuntimeError("enum {}.{} is unavailable".format(type_name, value_name))
    return value


def require_pie_stopped():
    subsystem = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem)
    if subsystem is None or subsystem.get_game_world() is not None:
        raise RuntimeError("PIE or Simulate must be stopped before changing session mode")
    return subsystem


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=ALLOWED_MODES)
    args = parser.parse_args()

    subsystem = require_pie_stopped()
    world = subsystem.get_editor_world()
    if world is None or world.get_path_name() != MAP_OBJECT_PATH:
        raise RuntimeError(
            "open {} before changing crowd mode; current={}".format(
                MAP_OBJECT_PATH, world.get_path_name() if world else None
            )
        )

    spawner_type = require_type("OpenMassCrowdSpawner")
    spawners = list(
        unreal.GameplayStatics.get_all_actors_of_class(world, spawner_type)
    )
    if len(spawners) != 1:
        raise RuntimeError("expected exactly one crowd spawner; found {}".format(len(spawners)))
    spawner = spawners[0]
    before = {
        "network_mode": str(spawner.get_editor_property("network_mode")),
        "population_count": int(spawner.get_editor_property("population_count")),
        "central_population_gate": str(
            spawner.get_editor_property("central_population_gate")
        ),
    }

    if args.mode == "local30":
        network_mode = enum_value(
            "OpenMassCrowdNetworkMode", "LOCAL_CERTIFIED_PATCH"
        )
        population = 30
        gate_name = "GATE30"
    else:
        network_mode = enum_value(
            "OpenMassCrowdNetworkMode", "CENTRAL_CERTIFIED_CACHE"
        )
        population = int(args.mode.removeprefix("central"))
        gate_name = "GATE{}".format(population)

    spawner.set_editor_property("network_mode", network_mode)
    spawner.set_editor_property("population_count", population)
    spawner.set_editor_property(
        "central_population_gate",
        enum_value("OpenMassCrowdCentralPopulationGate", gate_name),
    )
    after = {
        "network_mode": str(spawner.get_editor_property("network_mode")),
        "population_count": int(spawner.get_editor_property("population_count")),
        "central_population_gate": str(
            spawner.get_editor_property("central_population_gate")
        ),
    }
    payload = {
        "status": "PASS",
        "requested_mode": args.mode,
        "map": MAP_OBJECT_PATH,
        "spawner": spawner.get_path_name(),
        "pie_stopped": True,
        "before": before,
        "after": after,
        "saved_level": False,
        "session_only": True,
        "cold_restart_restores_saved_map": True,
    }
    print("OPEN_MASS_CROWD_SESSION_MODE=" + json.dumps(payload, sort_keys=True))


if __name__ == "__main__":
    main()
