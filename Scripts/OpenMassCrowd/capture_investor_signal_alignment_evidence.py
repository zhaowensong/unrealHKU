#!/usr/bin/env python3
"""Capture PIE evidence for the crowd/signal alignment regression.

Run this file through ``run_unreal_python_via_mcp.py`` while the investor
delivery demo is already in PIE.  The script changes only the transient viewer
camera and writes evidence under the project-relative ``Docs/Evidence`` tree.
It never moves or reconstructs signal geometry.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path

import unreal


WIDTH = 1920
HEIGHT = 1080
SHOT_FILES = {
    "overview": "05_people_and_persisted_signal_overview_2026-08-10.png",
    "roof-detail": "06_persisted_rooftop_landing_detail_2026-08-10.png",
    "layer-fixed": "08_legacy_floating_signal_layer_removed_2026-08-11.png",
    "association": "09_person_station_association_links_2026-08-11.png",
    "association-subtle": "10_person_station_association_subtle_2026-08-12.png",
}
SOURCE_PATTERN = re.compile(r"^SIG_Source_\d{2}_Direct_Roof$")
RAY_PATTERN = re.compile(
    r"^SIG_Ray_\d{3}_(?:Segment|RoofHit)_\d{2}_(?:Green|Yellow|Orange|Red)$"
)


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


def is_legacy_floating_signal(label):
    return (
        label.startswith("SIG_RaySegment_")
        or label.startswith("SIG_Node_")
        or label.startswith("SIG_Ray_HISM_")
        or (
            label.startswith("SIG_Source_")
            and SOURCE_PATTERN.fullmatch(label) is None
        )
    )


def vector_json(value):
    return {"x": float(value.x), "y": float(value.y), "z": float(value.z)}


def rotator_json(value):
    return {
        "pitch": float(value.pitch),
        "yaw": float(value.yaw),
        "roll": float(value.roll),
    }


def pie_world():
    subsystem = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem)
    world = subsystem.get_game_world()
    if world is not None:
        return world
    for candidate in unreal.ObjectIterator(unreal.World):
        if candidate.get_name().startswith("UEDPIE_"):
            return candidate
    raise RuntimeError("Investor PIE world is not running")


def bounds_center(points):
    if not points:
        raise RuntimeError("Cannot frame an empty point set")
    minimum = unreal.Vector(
        min(float(point.x) for point in points),
        min(float(point.y) for point in points),
        min(float(point.z) for point in points),
    )
    maximum = unreal.Vector(
        max(float(point.x) for point in points),
        max(float(point.y) for point in points),
        max(float(point.z) for point in points),
    )
    center = unreal.Vector(
        (float(minimum.x) + float(maximum.x)) * 0.5,
        (float(minimum.y) + float(maximum.y)) * 0.5,
        (float(minimum.z) + float(maximum.z)) * 0.5,
    )
    extent = max(
        (float(maximum.x) - float(minimum.x)) * 0.5,
        (float(maximum.y) - float(minimum.y)) * 0.5,
        8000.0,
    )
    return center, extent, minimum, maximum


def squared_xy(first, second):
    dx = float(first.x) - float(second.x)
    dy = float(first.y) - float(second.y)
    return dx * dx + dy * dy


def normalized_xy(vector):
    length = math.hypot(float(vector.x), float(vector.y))
    if length <= 0.001:
        return unreal.Vector(1.0, 0.0, 0.0)
    return unreal.Vector(float(vector.x) / length, float(vector.y) / length, 0.0)


def camera_for_overview(crowd_points, signal_points):
    points = crowd_points if crowd_points else signal_points
    center, extent, minimum, maximum = bounds_center(points)
    scale = max(12000.0, min(extent * 1.15, 30000.0))
    target = center + unreal.Vector(0.0, 0.0, 4200.0)
    location = center + unreal.Vector(-1.05 * scale, -1.18 * scale, 0.62 * scale)
    return location, target, minimum, maximum, None


def camera_for_roof_detail(sources, rays, crowd_points):
    if not sources:
        raise RuntimeError("No persisted SIG_Source_* actors were found")
    crowd_center = bounds_center(crowd_points)[0] if crowd_points else sources[0].get_actor_location()
    source = min(sources, key=lambda actor: squared_xy(actor.get_actor_location(), crowd_center))
    target = source.get_actor_location()
    nearest = sorted(
        rays,
        key=lambda actor: squared_xy(actor.get_actor_location(), target),
    )[:40]
    if nearest:
        mean = unreal.Vector(
            sum(float(actor.get_actor_location().x) for actor in nearest) / len(nearest),
            sum(float(actor.get_actor_location().y) for actor in nearest) / len(nearest),
            sum(float(actor.get_actor_location().z) for actor in nearest) / len(nearest),
        )
        flow = normalized_xy(mean - target)
    else:
        flow = unreal.Vector(1.0, 0.0, 0.0)
    side = unreal.Vector(-float(flow.y), float(flow.x), 0.0)
    location = target - flow * 13500.0 + side * 8500.0 + unreal.Vector(0.0, 0.0, 7200.0)
    target = target + unreal.Vector(0.0, 0.0, 250.0)
    ray_points = [actor.get_actor_location() for actor in nearest] or [target]
    _center, _extent, minimum, maximum = bounds_center(ray_points)
    return location, target, minimum, maximum, actor_label(source)


def camera_for_association(crowd_points, station_points):
    points = list(crowd_points) + list(station_points)
    _center, _extent, minimum, maximum = bounds_center(points)
    crowd_center = bounds_center(crowd_points)[0]
    target_station = min(
        station_points,
        key=lambda point: squared_xy(point, crowd_center),
    )
    # Stand just behind the street population and look toward its nearest live
    # rooftop endpoint. The association fan then reads clearly from person end
    # to roof end instead of being lost inside the city-wide propagation rays.
    location = crowd_center + unreal.Vector(-3500.0, 6500.0, 1800.0)
    target = target_station + unreal.Vector(0.0, 0.0, 250.0)
    return location, target, minimum, maximum, None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--shot", choices=tuple(SHOT_FILES), required=True)
    args = parser.parse_args(sys.argv[1:])

    world = pie_world()
    actors = list(unreal.GameplayStatics.get_all_actors_of_class(world, unreal.Actor))
    legacy_floating = [
        actor for actor in actors if is_legacy_floating_signal(actor_label(actor))
    ]
    visible_legacy_floating = []
    for actor in legacy_floating:
        component_visible = any(
            component.is_visible()
            and not bool(component.get_editor_property("hidden_in_game"))
            for component in actor.get_components_by_class(unreal.PrimitiveComponent)
        )
        if not actor_hidden(actor) and component_visible:
            visible_legacy_floating.append(actor_label(actor))
    sources = sorted(
        [actor for actor in actors if SOURCE_PATTERN.fullmatch(actor_label(actor))],
        key=actor_label,
    )
    rays = sorted(
        [actor for actor in actors if RAY_PATTERN.fullmatch(actor_label(actor))],
        key=actor_label,
    )
    crowd_class = getattr(unreal, "OpenMassCrowdCitySampleActor", None)
    crowd = (
        list(unreal.GameplayStatics.get_all_actors_of_class(world, crowd_class))
        if crowd_class is not None
        else []
    )
    crowd_points = [actor.get_actor_location() for actor in crowd]
    signal_points = [actor.get_actor_location() for actor in sources + rays]

    if len(sources) != 30 or len(rays) != 1920:
        raise RuntimeError(
            "Persisted signal inventory is {}, expected 30 sources + 1920 rays".format(
                len(sources) + len(rays)
            )
        )
    if visible_legacy_floating:
        raise RuntimeError(
            "Legacy floating signal layer is still visible: {} actors; samples={}".format(
                len(visible_legacy_floating), visible_legacy_floating[:20]
            )
        )

    spawner_class = getattr(unreal, "OpenMassCrowdSpawner", None)
    spawners = (
        list(unreal.GameplayStatics.get_all_actors_of_class(world, spawner_class))
        if spawner_class is not None
        else []
    )
    if len(spawners) != 1:
        raise RuntimeError("Expected one investor crowd spawner, found {}".format(len(spawners)))
    delivery = json.loads(spawners[0].get_investor_demo_evidence_snapshot())
    if args.shot.startswith("association"):
        # Stable person zero periodically enters the building and correctly
        # disconnects. Select person one for deterministic blue-link evidence.
        spawners[0].show_central_profile_by_stable_index(1)
    station_points = [
        unreal.Vector(
            float(item["validated_roof"]["x"]),
            float(item["validated_roof"]["y"]),
            float(item["validated_roof"]["z"]) + 4.0,
        )
        for item in delivery["stations"]["items"]
        if item["roof_validated"]
    ]
    if len(station_points) != 2:
        raise RuntimeError("Expected two live rooftop association endpoints")
    hism_class = unreal.HierarchicalInstancedStaticMeshComponent
    batches = [
        component
        for component in spawners[0].get_components_by_class(hism_class)
        if component.get_name().startswith("InvestorSignalBatch_")
    ]
    instance_count = sum(int(component.get_instance_count()) for component in batches)
    if len(batches) != 9 or instance_count != 1950:
        raise RuntimeError(
            "Runtime signal batches are {} / {}, expected 9 / 1950".format(
                len(batches), instance_count
            )
        )

    if args.shot == "overview":
        location, target, minimum, maximum, target_source = camera_for_overview(
            crowd_points, signal_points
        )
    elif args.shot.startswith("association"):
        portal = delivery["building"]["portal"]
        association_crowd_points = crowd_points or [
            unreal.Vector(
                float(portal["x"]),
                float(portal["y"]),
                float(portal["z"]),
            )
        ]
        location, target, minimum, maximum, target_source = camera_for_association(
            association_crowd_points, station_points
        )
    else:
        location, target, minimum, maximum, target_source = camera_for_roof_detail(
            sources, rays, crowd_points
        )
    rotation = unreal.MathLibrary.find_look_at_rotation(location, target)

    controller = unreal.GameplayStatics.get_player_controller(world, 0)
    pawn = unreal.GameplayStatics.get_player_pawn(world, 0)
    if pawn is not None:
        pawn.set_actor_location(location, False, True)
        pawn.set_actor_rotation(rotation, True)
    if controller is not None:
        controller.set_control_rotation(rotation)
    subsystem = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem)
    subsystem.set_level_viewport_camera_info(location, rotation)

    evidence_dir = (
        Path(unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_dir()))
        / "Docs"
        / "Evidence"
        / "InvestorDelivery"
    )
    evidence_dir.mkdir(parents=True, exist_ok=True)
    image_path = evidence_dir / SHOT_FILES[args.shot]
    report_path = image_path.with_suffix(".json")
    report = {
        "schema": "telecomtwin-investor-signal-alignment-evidence-v1",
        "shot": args.shot,
        "pie_world": world.get_path_name(),
        "persisted_signal_inventory": {
            "source_actor_count": len(sources),
            "ray_actor_count": len(rays),
        },
        "runtime_signal_batch": {
            "component_count": len(batches),
            "instance_count": instance_count,
            "source_policy": "exact persisted component world transforms",
            "runtime_debug_overlay": False,
        },
        "legacy_floating_signal_layer": {
            "loaded_actor_count": len(legacy_floating),
            "visible_actor_count": len(visible_legacy_floating),
            "passed": not visible_legacy_floating,
        },
        "crowd_visible_actor_count": len(crowd),
        "person_station_association": {
            "enabled": bool(delivery["network"]["association_visual_enabled"]),
            "connected_people": int(delivery["network"]["connected"]),
            "selected_link_style": delivery["network"]["selected_link_style"],
            "other_link_style": delivery["network"]["other_link_style"],
            "validated_rooftop_endpoints": len(station_points),
            "station_endpoint_offset_cm": float(
                delivery["network"]["station_endpoint_offset_cm"]
            ),
        },
        "camera": {
            "location": vector_json(location),
            "rotation": rotator_json(rotation),
            "target": vector_json(target),
            "framed_bounds_min": vector_json(minimum),
            "framed_bounds_max": vector_json(maximum),
            "target_source": target_source,
        },
        "image": str(Path("Docs") / "Evidence" / "InvestorDelivery" / SHOT_FILES[args.shot]),
    }
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    # In PIE on UE 5.7 the AutomationLibrary request can wait indefinitely
    # when the Editor was background-throttled. The viewport console path is
    # asynchronous and writes the same deterministic project-relative target.
    unreal.SystemLibrary.execute_console_command(
        world,
        'HighResShot 1 filename="{}"'.format(
            str(image_path).replace("\\", "/")
        ),
    )
    unreal.log_warning(
        "INVESTOR_SIGNAL_ALIGNMENT_EVIDENCE_REQUESTED={}".format(image_path)
    )


if __name__ == "__main__":
    main()
