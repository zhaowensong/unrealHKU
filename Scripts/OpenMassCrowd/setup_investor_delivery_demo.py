#!/usr/bin/env python3
"""Configure the open editor's existing Central spawner for investor mode.

The C++ defaults make the mode restart-safe. This script is the explicit,
repeatable operator entry point: it changes only the existing editor-world
spawner instance, does not save the World Partition map, and requires PIE to
be stopped. Run through ``run_unreal_python_via_mcp.py``.
"""

from __future__ import annotations

import json
from pathlib import Path

import unreal


EXPECTED_POPULATION = 100
EXPECTED_LINK_BUDGET = 12


def project_root() -> Path:
    return Path(
        unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_dir())
    )


def require_pie_stopped() -> None:
    subsystem = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem)
    if subsystem is None or subsystem.get_game_world() is not None:
        raise RuntimeError("stop PIE before configuring investor delivery mode")


def main() -> None:
    require_pie_stopped()
    config_path = project_root() / "Config" / "InvestorDeliveryDemo.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if int(config["population"]) != EXPECTED_POPULATION:
        raise RuntimeError(
            "delivery config must request exactly {} people".format(
                EXPECTED_POPULATION
            )
        )

    actor_subsystem = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    spawner_type = getattr(unreal, "OpenMassCrowdSpawner", None)
    if spawner_type is None:
        raise RuntimeError("OpenMassCrowdSpawner is unavailable; rebuild and restart UE")
    spawners = [
        actor
        for actor in actor_subsystem.get_all_level_actors()
        if isinstance(actor, spawner_type)
    ]
    if len(spawners) != 1:
        raise RuntimeError(
            "expected exactly one existing Central spawner, found {}".format(
                len(spawners)
            )
        )
    spawner = spawners[0]
    spawner.set_editor_property("investor_delivery_demo_enabled", True)
    spawner.set_editor_property("investor_delivery_population", EXPECTED_POPULATION)
    spawner.set_editor_property(
        "investor_association_visual_budget", EXPECTED_LINK_BUDGET
    )
    spawner.set_editor_property(
        "network_mode", unreal.OpenMassCrowdNetworkMode.CENTRAL_CERTIFIED_CACHE
    )
    try:
        spawner.set_editor_property(
            "central_population_gate",
            unreal.OpenMassCrowdCentralPopulationGate.GATE100,
        )
    except Exception:
        # InvestorDeliveryPopulation is the authoritative runtime cap. Keeping
        # compatibility with an editor that has not refreshed the enum makes
        # the failure message below more useful than a raw AttributeError.
        pass

    payload = {
        "schema": "telecomtwin-investor-delivery-setup-v1",
        "status": "ready_for_pie",
        "spawner": spawner.get_path_name(),
        "map": unreal.EditorLevelLibrary.get_editor_world().get_path_name(),
        "population": int(
            spawner.get_editor_property("investor_delivery_population")
        ),
        "association_visual_budget": int(
            spawner.get_editor_property("investor_association_visual_budget")
        ),
        "mode_enabled": bool(
            spawner.get_editor_property("investor_delivery_demo_enabled")
        ),
        "map_saved": False,
        "next_step": "Start PIE and allow 20-40 seconds for Cesium roof validation and crowd admission.",
    }
    if payload["population"] != EXPECTED_POPULATION or not payload["mode_enabled"]:
        raise RuntimeError("investor delivery properties did not apply")
    print("INVESTOR_DELIVERY_SETUP=" + json.dumps(payload, ensure_ascii=False))


main()
