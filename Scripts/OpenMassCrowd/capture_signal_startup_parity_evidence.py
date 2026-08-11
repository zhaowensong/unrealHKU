#!/usr/bin/env python3
"""Capture the same persisted Central signal layer before and during PIE."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import unreal


SOURCE_PATTERN = re.compile(r"^SIG_Source_\d{2}_Direct_Roof$")
RAY_PATTERN = re.compile(
    r"^SIG_Ray_\d{3}_(?:Segment|RoofHit)_\d{2}_(?:Green|Yellow|Orange|Red)$"
)
CAMERA_LOCATION = unreal.Vector(-201917.067084, 269416.464474, 36191.010785)
# Unreal's Python Rotator constructor is ordered roll, pitch, yaw.
CAMERA_ROTATION = unreal.Rotator(0.0, -23.540503, -139.236006)
FILES = {
    "editor": "13_signal_before_pie_same_view_2026-08-12.png",
    "pie": "14_signal_during_pie_same_view_2026-08-12.png",
}


def actor_label(actor):
    try:
        return str(actor.get_actor_label())
    except Exception:
        return str(actor.get_name())


def actor_hidden(actor):
    try:
        return bool(actor.is_hidden())
    except Exception:
        return bool(actor.get_editor_property("hidden"))


def actor_visible(actor):
    components = actor.get_components_by_class(unreal.PrimitiveComponent)
    return bool(
        not actor_hidden(actor)
        and any(
            component.is_visible()
            and not bool(component.get_editor_property("hidden_in_game"))
            for component in components
        )
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=tuple(FILES), required=True)
    args = parser.parse_args(sys.argv[1:])

    editor_subsystem = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem)
    if args.mode == "pie":
        world = editor_subsystem.get_game_world()
        if world is None or "UEDPIE_" not in world.get_path_name().upper():
            raise RuntimeError("active PIE world required")
    else:
        world = editor_subsystem.get_editor_world()
        if world is None or "UEDPIE_" in world.get_path_name().upper():
            raise RuntimeError("editor world without PIE required")

    actors = list(unreal.GameplayStatics.get_all_actors_of_class(world, unreal.Actor))
    sources = [actor for actor in actors if SOURCE_PATTERN.fullmatch(actor_label(actor))]
    rays = [actor for actor in actors if RAY_PATTERN.fullmatch(actor_label(actor))]
    persisted = sources + rays
    visible = [actor for actor in persisted if actor_visible(actor)]
    if len(sources) != 30 or len(rays) != 1920 or len(visible) != 1950:
        raise RuntimeError(
            "persisted signal mismatch: sources={} rays={} visible={}".format(
                len(sources), len(rays), len(visible)
            )
        )

    runtime_batch_count = 0
    runtime_batch_instances = 0
    if args.mode == "pie":
        spawners = unreal.GameplayStatics.get_all_actors_of_class(
            world, unreal.OpenMassCrowdSpawner
        )
        if len(spawners) != 1:
            raise RuntimeError("one OpenMassCrowdSpawner required")
        for component in spawners[0].get_components_by_class(
            unreal.HierarchicalInstancedStaticMeshComponent
        ):
            if component.get_name().startswith("InvestorSignalBatch_"):
                runtime_batch_count += 1
                runtime_batch_instances += int(component.get_instance_count())
        if runtime_batch_count or runtime_batch_instances:
            raise RuntimeError("runtime rebuilt the persisted signal layer")

    editor_subsystem.set_level_viewport_camera_info(
        CAMERA_LOCATION, CAMERA_ROTATION
    )
    if args.mode == "pie":
        controller = unreal.GameplayStatics.get_player_controller(world, 0)
        pawn = unreal.GameplayStatics.get_player_pawn(world, 0)
        if pawn is not None:
            pawn.set_actor_location(CAMERA_LOCATION, False, True)
            pawn.set_actor_rotation(CAMERA_ROTATION, True)
        if controller is not None:
            controller.set_control_rotation(CAMERA_ROTATION)

    evidence_dir = (
        Path(unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_dir()))
        / "Docs"
        / "Evidence"
        / "InvestorDelivery"
    )
    evidence_dir.mkdir(parents=True, exist_ok=True)
    image_path = evidence_dir / FILES[args.mode]
    report = {
        "schema": "telecomtwin-signal-startup-parity-v1",
        "mode": args.mode,
        "world": world.get_path_name(),
        "source_actor_count": len(sources),
        "ray_actor_count": len(rays),
        "visible_original_actor_count": len(visible),
        "runtime_batch_component_count": runtime_batch_count,
        "runtime_batch_instance_count": runtime_batch_instances,
        "camera_location": {
            "x": float(CAMERA_LOCATION.x),
            "y": float(CAMERA_LOCATION.y),
            "z": float(CAMERA_LOCATION.z),
        },
        "camera_rotation": {
            "pitch": float(CAMERA_ROTATION.pitch),
            "yaw": float(CAMERA_ROTATION.yaw),
            "roll": float(CAMERA_ROTATION.roll),
        },
        "image": str(
            Path("Docs") / "Evidence" / "InvestorDelivery" / FILES[args.mode]
        ),
    }
    image_path.with_suffix(".json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if args.mode == "editor":
        # HighResShot on an editor world can wait for a viewport redraw for a
        # long time.  AutomationLibrary captures that viewport explicitly and
        # gives Cesium a small final settling window.
        # Keep the async task alive after the MCP command returns.
        unreal._signal_startup_parity_capture_task = (
            unreal.AutomationLibrary.take_high_res_screenshot(
                1920,
                1080,
                str(image_path).replace("\\", "/"),
                None,
                False,
                False,
                unreal.ComparisonTolerance.LOW,
                "Persisted signal layer before PIE",
                5.0,
                True,
            )
        )
    else:
        unreal.SystemLibrary.execute_console_command(
            world,
            'HighResShot 1 filename="{}"'.format(
                str(image_path).replace("\\", "/")
            ),
        )
    unreal.log_warning("SIGNAL_STARTUP_PARITY_CAPTURE={}".format(image_path))


if __name__ == "__main__":
    main()
