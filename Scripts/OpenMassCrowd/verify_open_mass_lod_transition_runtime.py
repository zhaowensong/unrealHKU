"""Prove Low -> High -> Low Mass representation swaps in the active PIE demo."""

from __future__ import annotations

import builtins
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import unreal


CALLBACK_KEY = "_hk_open_mass_lod_verify_handle"
STATE_KEY = "_hk_open_mass_lod_verify_state"
TARGET_POPULATION = 30
STAGE_TIMEOUT_SECONDS = 15.0
OUTPUT_DIR = (
    Path(unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_dir()))
    / "Docs"
    / "Evidence"
    / "OpenMassCrowd"
)
REPORT_PATH = OUTPUT_DIR / "open_mass_lod_transition_runtime_latest.json"


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def game_world():
    return unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_game_world()


def actors_of_class(world, actor_class):
    if world is None or actor_class is None:
        return []
    return list(unreal.GameplayStatics.get_all_actors_of_class(world, actor_class))


def actor_hidden(actor):
    try:
        return bool(actor.get_editor_property("hidden"))
    except Exception:
        return bool(actor.is_hidden())


def snapshot(world):
    spawner_class = getattr(unreal, "OpenMassCrowdSpawner", None)
    proxy_class = getattr(unreal, "OpenMassCrowdCitySampleActor", None)
    spawners = actors_of_class(world, spawner_class)
    proxies = [actor for actor in actors_of_class(world, proxy_class) if not actor_hidden(actor)]
    if len(spawners) != 1:
        return None
    spawner = spawners[0]
    seeds = []
    classes = []
    for proxy in proxies:
        try:
            seed = int(proxy.get_mass_appearance_seed())
        except Exception:
            seed = -1
        if seed >= 0:
            seeds.append(seed)
        classes.append(proxy.get_class().get_name())
    return {
        "spawner": spawner,
        "high": int(spawner.get_current_high_res_representation_count()),
        "low": int(spawner.get_current_low_res_representation_count()),
        "active_proxy_count": len(proxies),
        "mass_seeds": sorted(seeds),
        "representation_classes": sorted(set(classes)),
    }


def set_camera(world, location, target):
    controller = unreal.GameplayStatics.get_player_controller(world, 0)
    pawn = controller.get_controlled_pawn() if controller is not None else None
    if controller is None or pawn is None:
        raise RuntimeError("PIE player controller/pawn is unavailable")
    rotation = unreal.MathLibrary.find_look_at_rotation(location, target)
    pawn.set_actor_location(location, False, False)
    controller.set_control_rotation(rotation)


def take_shot(filename):
    path = OUTPUT_DIR / filename
    unreal.AutomationLibrary.take_high_res_screenshot(
        1600,
        900,
        str(path),
        camera=None,
        mask_enabled=False,
        capture_hdr=False,
        delay=0.0,
        force_game_view=True,
    )
    return str(path)


def restore_camera(state):
    world = game_world()
    controller = (
        unreal.GameplayStatics.get_player_controller(world, 0)
        if world is not None
        else None
    )
    pawn = controller.get_controlled_pawn() if controller is not None else None
    if pawn is not None and state.get("original_pawn_location") is not None:
        pawn.set_actor_location(state["original_pawn_location"], False, False)
    if controller is not None and state.get("original_control_rotation") is not None:
        controller.set_control_rotation(state["original_control_rotation"])
    original = state.get("original_camera")
    if original:
        unreal.get_editor_subsystem(
            unreal.UnrealEditorSubsystem
        ).set_level_viewport_camera_info(original[0], original[1])


def finish(state, reason):
    restore_camera(state)
    observations = state.get("observations", [])
    pairs = [[item["high"], item["low"]] for item in observations]
    seed_sets = [set(item["mass_seeds"]) for item in observations]
    identities_preserved = (
        len(seed_sets) == 3
        and all(len(item) == TARGET_POPULATION for item in seed_sets)
        and seed_sets[0] == seed_sets[1] == seed_sets[2]
    )
    overall = (
        reason == "completed"
        and pairs == [[0, 30], [30, 0], [0, 30]]
        and identities_preserved
        and not state.get("errors")
    )
    report = {
        "schema_version": 1,
        "generated_utc": utc_now(),
        "engine_version": unreal.SystemLibrary.get_engine_version(),
        "overall_passed": overall,
        "completion_reason": reason,
        "expected_sequence": [[0, 30], [30, 0], [0, 30]],
        "observed_sequence": pairs,
        "stable_mass_identities_preserved": identities_preserved,
        "observations": observations,
        "screenshots": state.get("screenshots", []),
        "errors": state.get("errors", []),
    }
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + chr(10),
        encoding="utf-8",
    )
    marker = {
        "overall_passed": overall,
        "completion_reason": reason,
        "observed_sequence": pairs,
        "stable_mass_identities_preserved": identities_preserved,
        "report": str(REPORT_PATH),
    }
    message = "OPEN_MASS_LOD_TRANSITION_VERIFY=" + json.dumps(
        marker, ensure_ascii=False, sort_keys=True
    )
    if overall:
        unreal.log_warning(message)
    else:
        unreal.log_error(message)

    handle = state.get("handle")
    if handle is not None:
        unreal.unregister_slate_post_tick_callback(handle)
    if getattr(builtins, CALLBACK_KEY, None) == handle:
        delattr(builtins, CALLBACK_KEY)
    if getattr(builtins, STATE_KEY, None) is state:
        delattr(builtins, STATE_KEY)


def configure_view(state, world, snap, target_pair):
    center = snap["spawner"].get_actor_location()
    if target_pair == (30, 0):
        location = center + unreal.Vector(0.0, -200.0, 500.0)
    else:
        location = center + unreal.Vector(0.0, -6000.0, 1400.0)
    set_camera(world, location, center + unreal.Vector(0.0, 0.0, 80.0))
    state["target_pair"] = target_pair
    state["stage_deadline"] = time.monotonic() + STAGE_TIMEOUT_SECONDS


def tick(_delta_seconds):
    state = getattr(builtins, STATE_KEY, None)
    if state is None:
        return
    now = time.monotonic()
    if now < state["next_action"]:
        return
    try:
        world = game_world()
        snap = snapshot(world)
        if snap is None or snap["active_proxy_count"] != TARGET_POPULATION:
            if now > state["overall_deadline"]:
                finish(state, "population_timeout")
            return

        stage = state["stage"]
        if stage == 0:
            subsystem = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem)
            try:
                state["original_camera"] = subsystem.get_level_viewport_camera_info()
            except Exception:
                state["original_camera"] = None
            controller = unreal.GameplayStatics.get_player_controller(world, 0)
            pawn = controller.get_controlled_pawn() if controller is not None else None
            if controller is None or pawn is None:
                raise RuntimeError("PIE player controller/pawn is unavailable")
            state["original_pawn_location"] = pawn.get_actor_location()
            state["original_control_rotation"] = controller.get_control_rotation()
            configure_view(state, world, snap, (0, 30))
            state["stage"] = 1
            state["next_action"] = now + 0.25
            return

        if stage in (1, 3, 5):
            target_pair = state["target_pair"]
            pair = (snap["high"], snap["low"])
            identities_ready = len(snap["mass_seeds"]) == TARGET_POPULATION
            if pair != target_pair or not identities_ready:
                if now > state["stage_deadline"]:
                    finish(state, "transition_timeout_{}_{}".format(*target_pair))
                return

            label = "low" if target_pair == (0, 30) else "high"
            filename = {
                1: "08_lod_low_30.png",
                3: "09_lod_high_30.png",
                5: "10_lod_low_30_return.png",
            }[stage]
            # Screenshot capture pumps Slate recursively. Advance first so a
            # re-entrant callback cannot record the same transition repeatedly.
            state["stage"] = stage + 1
            state["next_action"] = now + 2.0
            state["observations"].append(
                {
                    "stage": label,
                    "high": snap["high"],
                    "low": snap["low"],
                    "active_proxy_count": snap["active_proxy_count"],
                    "mass_seeds": snap["mass_seeds"],
                    "representation_classes": snap["representation_classes"],
                }
            )
            state["screenshots"].append(take_shot(filename))
            return

        if stage == 2:
            configure_view(state, world, snap, (30, 0))
            state["stage"] = 3
            state["next_action"] = now + 0.25
            return

        if stage == 4:
            configure_view(state, world, snap, (0, 30))
            state["stage"] = 5
            state["next_action"] = now + 0.25
            return

        finish(state, "completed")
    except Exception as error:
        state["errors"].append(repr(error))
        finish(state, "callback_exception")


def main():
    old_handle = getattr(builtins, CALLBACK_KEY, None)
    if old_handle is not None:
        try:
            unreal.unregister_slate_post_tick_callback(old_handle)
        except Exception:
            pass
    now = time.monotonic()
    state = {
        "started": now,
        "overall_deadline": now + 75.0,
        "next_action": now,
        "stage": 0,
        "stage_deadline": now + STAGE_TIMEOUT_SECONDS,
        "target_pair": None,
        "original_camera": None,
        "original_pawn_location": None,
        "original_control_rotation": None,
        "observations": [],
        "screenshots": [],
        "errors": [],
        "handle": None,
    }
    state["handle"] = unreal.register_slate_post_tick_callback(tick)
    setattr(builtins, CALLBACK_KEY, state["handle"])
    setattr(builtins, STATE_KEY, state)
    unreal.log_warning("OPEN_MASS_LOD_TRANSITION_VERIFY_STARTED")


main()
