"""Capture wide, variation, and foot-level City Sample runtime evidence.

Run through ``run_unreal_python_via_mcp.py`` while the verified PIE session is
active.  The script moves only the editor viewport camera and writes three PNG
files under ``Docs/Evidence/OpenMassCrowd``; it does not modify the level.
"""

from __future__ import annotations

import builtins
import time
from pathlib import Path

import unreal


CALLBACK_KEY = "_hk_open_mass_city_sample_capture_handle"
STATE_KEY = "_hk_open_mass_city_sample_capture_state"
OUTPUT_DIR = (
    Path(unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_dir()))
    / "Docs"
    / "Evidence"
    / "OpenMassCrowd"
)


def game_world():
    return unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_game_world()


def active_proxies(world):
    actor_class = getattr(unreal, "OpenMassCrowdCitySampleActor", None)
    if world is None or actor_class is None:
        return []
    return [
        actor
        for actor in unreal.GameplayStatics.get_all_actors_of_class(world, actor_class)
        if not actor.get_editor_property("hidden")
    ]


def average_location(actors):
    count = float(len(actors))
    return unreal.Vector(
        sum(actor.get_actor_location().x for actor in actors) / count,
        sum(actor.get_actor_location().y for actor in actors) / count,
        sum(actor.get_actor_location().z for actor in actors) / count,
    )


def set_camera(location, target):
    rotation = unreal.MathLibrary.find_look_at_rotation(location, target)
    unreal.get_editor_subsystem(
        unreal.UnrealEditorSubsystem
    ).set_level_viewport_camera_info(location, rotation)


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
    unreal.log_warning("OPEN_MASS_CITY_SAMPLE_SCREENSHOT_REQUESTED={}".format(path))


def finish(state):
    handle = state.get("handle")
    if handle is not None:
        unreal.unregister_slate_post_tick_callback(handle)
    if getattr(builtins, CALLBACK_KEY, None) == handle:
        delattr(builtins, CALLBACK_KEY)
    if getattr(builtins, STATE_KEY, None) is state:
        delattr(builtins, STATE_KEY)
    unreal.log_warning("OPEN_MASS_CITY_SAMPLE_SCREENSHOTS_COMPLETE")


def tick_capture(_delta_seconds):
    state = getattr(builtins, STATE_KEY, None)
    if state is None:
        return
    now = time.monotonic()
    if now < state["next_action"]:
        return

    world = game_world()
    actors = active_proxies(world)
    if len(actors) != 30:
        if now - state["started"] > 120.0:
            unreal.log_error(
                "OPEN_MASS_CITY_SAMPLE_SCREENSHOTS_ABORT active_proxies={}".format(
                    len(actors)
                )
            )
            finish(state)
        return

    center = average_location(actors)
    stage = state["stage"]
    # Set the next state before requesting a high-resolution screenshot.  The
    # editor can pump Slate recursively during that request; pre-advancing here
    # prevents the same shot from being queued repeatedly by a re-entrant tick.
    state["stage"] = stage + 1
    state["next_action"] = now + (2.0 if stage in {1, 3, 5} else 0.7)
    if stage == 0:
        set_camera(
            center + unreal.Vector(-2100.0, -2400.0, 1150.0),
            center + unreal.Vector(0.0, 0.0, 80.0),
        )
    elif stage == 1:
        take_shot("04_city_sample_30_wide.png")
    elif stage == 2:
        set_camera(
            center + unreal.Vector(-560.0, -760.0, 300.0),
            center + unreal.Vector(0.0, 0.0, 80.0),
        )
    elif stage == 3:
        take_shot("05_city_sample_29_variants_close.png")
    elif stage == 4:
        focus = min(
            actors,
            key=lambda actor: (
                actor.get_actor_location().x - center.x
            ) ** 2
            + (actor.get_actor_location().y - center.y) ** 2,
        ).get_actor_location()
        set_camera(
            focus + unreal.Vector(-280.0, -360.0, 125.0),
            focus + unreal.Vector(0.0, 0.0, 72.0),
        )
    elif stage == 5:
        take_shot("06_city_sample_cesium_foot_grounding.png")
    else:
        finish(state)
        return


def main():
    old_handle = getattr(builtins, CALLBACK_KEY, None)
    if old_handle is not None:
        try:
            unreal.unregister_slate_post_tick_callback(old_handle)
        except Exception:
            pass

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    state = {
        "started": time.monotonic(),
        "next_action": time.monotonic(),
        "stage": 0,
        "handle": None,
    }
    state["handle"] = unreal.register_slate_post_tick_callback(tick_capture)
    setattr(builtins, CALLBACK_KEY, state["handle"])
    setattr(builtins, STATE_KEY, state)
    unreal.log_warning("OPEN_MASS_CITY_SAMPLE_SCREENSHOTS_STARTED")


main()
