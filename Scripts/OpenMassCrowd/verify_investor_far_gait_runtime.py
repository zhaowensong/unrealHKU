#!/usr/bin/env python3
"""Verify one fixed pedestrian keeps VAT gait at a very distant camera."""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from run_unreal_python_via_mcp import execute_python


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = (
    ROOT
    / "Docs"
    / "Evidence"
    / "InvestorDelivery"
    / "far_gait_runtime_latest.json"
)
MARKER = "INVESTOR_FAR_GAIT="
STATE_KEY = "_telecomtwin_far_gait_verifier_state"


def unreal_json(code: str) -> dict[str, Any]:
    response = execute_python(code, timeout_seconds=30.0)
    if response.get("status") != "success":
        raise RuntimeError(response.get("message") or "Unreal command failed")
    output = str((response.get("result") or {}).get("output") or "")
    for line in reversed(output.splitlines()):
        if line.startswith(MARKER):
            return json.loads(line[len(MARKER) :])
    raise RuntimeError("far-gait marker missing: {!r}".format(output))


def position_camera(distance_m: float) -> dict[str, Any]:
    distance_cm = float(distance_m) * 100.0
    return unreal_json(
        f"""
import builtins
import json
import unreal

world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_game_world()
if world is None or "UEDPIE_" not in world.get_path_name().upper():
    raise RuntimeError("active PIE world required")
spawner = unreal.GameplayStatics.get_all_actors_of_class(
    world, unreal.OpenMassCrowdSpawner
)[0]
controller = unreal.GameplayStatics.get_player_controller(world, 0)
pawn = controller.get_controlled_pawn() or controller.get_spectator_pawn()
if pawn is None:
    raise RuntimeError("PIE viewer unavailable")
candidates = []
for stable_index in range(int(spawner.get_spawned_entity_count())):
    item = json.loads(
        spawner.get_central_vat_animation_evidence_snapshot_for_stable_index(
            stable_index
        )
    )
    if item.get("valid") and item.get("animation_active"):
        candidates.append((float(item.get("speed_cm_s", 0.0)), stable_index, item))
if not candidates:
    raise RuntimeError("no live VAT pedestrian available before camera move")
candidates.sort(reverse=True)
_speed, stable_index, target_snapshot = candidates[0]
point = target_snapshot["location"]
target = unreal.Vector(float(point["x"]), float(point["y"]), float(point["z"]))
setattr(builtins, {STATE_KEY!r}, {{
    "transform": pawn.get_actor_transform(),
    "control_rotation": controller.get_control_rotation(),
    "fov": float(controller.player_camera_manager.get_fov_angle()),
    "stable_index": int(stable_index),
}})
camera = target + unreal.Vector({distance_cm!r}, 0.0, 12000.0)
rotation = unreal.MathLibrary.find_look_at_rotation(
    camera, target + unreal.Vector(0.0, 0.0, 90.0)
)
pawn.set_actor_location(camera, False, True)
pawn.set_actor_rotation(rotation, True)
controller.set_control_rotation(rotation)
unreal.SystemLibrary.execute_console_command(world, "fov 35")
print({MARKER!r} + json.dumps({{
    "stable_index": int(stable_index),
    "person_id": str(target_snapshot["person_id"]),
    "requested_distance_m": {float(distance_m)!r},
}}, sort_keys=True))
"""
    )


def snapshot() -> dict[str, Any]:
    return unreal_json(
        f"""
import builtins
import json
import unreal

world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_game_world()
spawner = unreal.GameplayStatics.get_all_actors_of_class(
    world, unreal.OpenMassCrowdSpawner
)[0]
state = getattr(builtins, {STATE_KEY!r}, None)
if not isinstance(state, dict):
    raise RuntimeError("far-gait verifier camera state missing")
item = json.loads(
    spawner.get_central_vat_animation_evidence_snapshot_for_stable_index(
        int(state["stable_index"])
    )
)
print({MARKER!r} + json.dumps({{
    "target": item,
    "high_actors": int(spawner.get_current_high_res_representation_count()),
    "low_actors": int(spawner.get_current_low_res_representation_count()),
    "vat_actors": int(spawner.get_central_vat_representation_count()),
    "spawned": int(spawner.get_spawned_entity_count()),
}}, sort_keys=True))
"""
    )


def vat_component_limits() -> dict[str, Any]:
    return unreal_json(
        f"""
import json
import unreal

world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_game_world()
items = []
for actor in unreal.GameplayStatics.get_all_actors_of_class(world, unreal.Actor):
    for component in actor.get_components_by_class(
        unreal.InstancedStaticMeshComponent
    ):
        mesh = component.get_editor_property("static_mesh")
        if mesh is None or "OpenMassCrowd" not in mesh.get_path_name():
            continue
        items.append({{
            "mesh": mesh.get_path_name(),
            "instances": int(component.get_instance_count()),
            "wpo_disable_distance_cm": int(component.get_editor_property(
                "world_position_offset_disable_distance"
            )),
            "instance_end_cull_distance_cm": int(component.get_editor_property(
                "instance_end_cull_distance"
            )),
            "cached_max_draw_distance_cm": float(component.get_editor_property(
                "cached_max_draw_distance"
            )),
        }})
print({MARKER!r} + json.dumps({{
    "component_count": len(items),
    "total_instances": sum(item["instances"] for item in items),
    "minimum_end_cull_distance_cm": min(
        (item["instance_end_cull_distance_cm"] for item in items),
        default=0,
    ),
    "maximum_wpo_disable_distance_cm": max(
        (item["wpo_disable_distance_cm"] for item in items),
        default=-1,
    ),
    "maximum_cached_draw_distance_cm": max(
        (item["cached_max_draw_distance_cm"] for item in items),
        default=-1.0,
    ),
    "items": items,
}}, sort_keys=True))
"""
    )


def restore_camera() -> None:
    unreal_json(
        f"""
import builtins
import json
import unreal

world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_game_world()
controller = unreal.GameplayStatics.get_player_controller(world, 0)
pawn = controller.get_controlled_pawn() or controller.get_spectator_pawn()
state = getattr(builtins, {STATE_KEY!r}, None)
restored = False
if isinstance(state, dict) and pawn is not None:
    pawn.set_actor_transform(state["transform"], False, True)
    controller.set_control_rotation(state["control_rotation"])
    unreal.SystemLibrary.execute_console_command(
        world, "fov {{}}".format(float(state["fov"]))
    )
    restored = True
setattr(builtins, {STATE_KEY!r}, None)
print({MARKER!r} + json.dumps({{"restored": restored}}, sort_keys=True))
"""
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--distance-m", type=float, default=1500.0)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if args.distance_m < 1000.0:
        raise ValueError("distance-m must exercise the former 1 km Off boundary")

    component_limits = vat_component_limits()
    positioned = position_camera(args.distance_m)
    first = second = None
    try:
        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline:
            candidate = snapshot()
            target = candidate.get("target") or {}
            if (
                target.get("valid")
                and str(target.get("representation")) == "VAT"
                and float(target.get("distance_m", 0.0))
                >= args.distance_m * 0.95
            ):
                first = candidate
                break
            time.sleep(0.5)
        if first is None:
            first = snapshot()
            raise RuntimeError(
                "target did not remain VAT beyond old cutoff: {}".format(first)
            )

        start_world_time = float(first["target"]["world_time_seconds"])
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            candidate = snapshot()
            if (
                float(candidate["target"].get("world_time_seconds", 0.0))
                - start_world_time
                >= 1.0
            ):
                second = candidate
                break
            time.sleep(0.2)
        if second is None:
            raise RuntimeError("PIE world did not advance for far-gait sample")
    finally:
        restore_camera()

    first_target = first["target"]
    second_target = second["target"]
    frame_span = max(
        float(first_target["end_frame"]) - float(first_target["start_frame"]),
        1.0,
    )
    frame_advance = (
        float(second_target["current_frame"])
        - float(first_target["current_frame"])
    ) % frame_span
    checks = {
        "same_person": (
            first_target["stable_index"] == second_target["stable_index"]
            and first_target["person_id"] == second_target["person_id"]
        ),
        "beyond_old_one_km_cutoff": (
            float(first_target["distance_m"]) >= args.distance_m * 0.95
            and float(second_target["distance_m"]) >= args.distance_m * 0.95
        ),
        "vat_representation_retained": (
            first_target["representation"] == "VAT"
            and second_target["representation"] == "VAT"
        ),
        "animation_active": (
            bool(first_target["animation_active"])
            and bool(second_target["animation_active"])
        ),
        "animation_frame_advanced": frame_advance >= 1.0,
        "population_still_represented": (
            int(second["vat_actors"])
            + int(second["high_actors"])
            + int(second["low_actors"])
            >= 95
        ),
        "vat_wpo_never_distance_disabled": (
            int(component_limits["component_count"]) >= 24
            and int(component_limits["maximum_wpo_disable_distance_cm"]) == 0
        ),
        "vat_instances_not_culled_before_three_km": (
            int(component_limits["minimum_end_cull_distance_cm"]) >= 300000
            and float(component_limits["maximum_cached_draw_distance_cm"]) == 0.0
        ),
    }
    report = {
        "schema": "telecomtwin-investor-far-gait-runtime-v1",
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "requested_distance_m": args.distance_m,
        "vat_component_limits": component_limits,
        "positioned": positioned,
        "first": first,
        "second": second,
        "frame_advance": round(frame_advance, 6),
        "checks": checks,
        "passed": all(checks.values()),
        "camera_restored": True,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
