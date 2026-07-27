#!/usr/bin/env python3
"""Verify the four Central pedestrian experience requirements in live PIE.

This controller runs outside Unreal so the game keeps ticking between two VAT
frame samples.  It never edits the map or moves a Mass entity.
"""

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
    / "OpenMassCrowd"
    / "central_crowd_experience_runtime_latest.json"
)
MARKER = "OPEN_MASS_CROWD_EXPERIENCE_SNAPSHOT="


def unreal_snapshot(show_profile: bool, profile_index: int) -> dict[str, Any]:
    code = f"""
import json
import re
import unreal

world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_game_world()
if world is None or "UEDPIE_" not in world.get_path_name().upper():
    raise RuntimeError("an active PIE world is required")
spawners = unreal.GameplayStatics.get_all_actors_of_class(
    world, unreal.OpenMassCrowdSpawner
)
if len(spawners) != 1:
    raise RuntimeError("expected one OpenMassCrowdSpawner, found {{}}".format(len(spawners)))
spawner = spawners[0]
if {show_profile!r}:
    if not spawner.show_central_profile_by_stable_index({profile_index}):
        raise RuntimeError("failed to show stable profile {profile_index}")
source_pattern = re.compile(r"^SIG_Source_\\d{{2}}_Direct_Roof$")
ray_pattern = re.compile(
    r"^SIG_Ray_\\d{{3}}_(?:Segment|RoofHit)_\\d{{2}}_(Green|Yellow|Orange|Red)$"
)
source_labels = []
ray_labels = []
ray_geometry_count = 0
color_geometry_counts = {{name: 0 for name in ("Green", "Yellow", "Orange", "Red")}}
for actor in unreal.GameplayStatics.get_all_actors_of_class(world, unreal.Actor):
    try:
        label = str(actor.get_actor_label())
    except Exception:
        label = str(actor.get_name())
    if source_pattern.fullmatch(label):
        source_labels.append(label)
    ray_match = ray_pattern.fullmatch(label)
    if ray_match is None:
        continue
    ray_labels.append(label)
    actor_geometry = 0
    for component in actor.get_components_by_class(unreal.StaticMeshComponent):
        try:
            mesh = component.get_editor_property("static_mesh")
        except Exception:
            mesh = component.get_static_mesh()
        if mesh is None:
            continue
        try:
            hidden = bool(component.get_editor_property("hidden_in_game"))
        except Exception:
            hidden = False
        if not component.is_visible() or hidden:
            continue
        if isinstance(component, unreal.InstancedStaticMeshComponent):
            actor_geometry += int(component.get_instance_count())
        else:
            actor_geometry += 1
    ray_geometry_count += actor_geometry
    color_geometry_counts[ray_match.group(1)] += actor_geometry
payload = {{
    "world": world.get_path_name(),
    "population": {{
        "target": int(spawner.get_central_admission_target_count()),
        "admitted": int(spawner.get_central_admitted_entity_count()),
        "simulated": int(spawner.get_central_simulated_entity_count()),
        "represented": int(spawner.get_central_represented_entity_count()),
    }},
    "motion": {{
        "expected_moving": int(spawner.get_central_expected_moving_entity_count()),
        "moving": int(spawner.get_central_moving_entity_count()),
        "stuck": int(spawner.get_central_stuck_entity_count()),
        "completed_path_legs": int(spawner.get_completed_trip_count()),
        "route_assignments": int(spawner.get_route_assignment_count()),
    }},
    "ground_and_clearance": {{
        "unsupported": int(spawner.get_current_unsupported_visual_count()),
        "overlap_pairs": int(spawner.get_central_severe_overlap_pair_count()),
        "overlap_agents": int(spawner.get_central_severe_overlap_agent_count()),
        "invalid_positions": int(spawner.get_central_invalid_position_observation_count()),
        "admission_violations": int(spawner.get_central_admission_clearance_violation_count()),
        "minimum_observed_center_cm": float(
            spawner.get_central_minimum_observed_entity_center_distance_cm()
        ),
    }},
    "vat": json.loads(spawner.get_central_vat_animation_evidence_snapshot()),
    "profile": json.loads(spawner.get_central_profile_evidence_snapshot()),
    "telecom_regression": {{
        "source_actor_count": len(source_labels),
        "unique_source_label_count": len(set(source_labels)),
        "ray_actor_count": len(ray_labels),
        "unique_ray_label_count": len(set(ray_labels)),
        "ray_geometry_count": ray_geometry_count,
        "color_geometry_counts": color_geometry_counts,
    }},
}}
print({MARKER!r} + json.dumps(payload, ensure_ascii=False, sort_keys=True))
"""
    response = execute_python(code, timeout_seconds=30.0)
    if response.get("status") != "success":
        raise RuntimeError(response.get("message") or "Unreal snapshot failed")
    output = str((response.get("result") or {}).get("output") or "")
    for line in reversed(output.splitlines()):
        if line.startswith(MARKER):
            return json.loads(line[len(MARKER) :])
    raise RuntimeError(f"snapshot marker missing from Unreal output: {output!r}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--profile-index", type=int, default=137)
    parser.add_argument("--sample-delay", type=float, default=2.0)
    args = parser.parse_args()

    first = unreal_snapshot(True, args.profile_index)
    time.sleep(max(args.sample_delay, 0.5))
    second = unreal_snapshot(False, args.profile_index)

    first_vat = first["vat"]
    second_vat = second["vat"]
    frame_span = max(
        float(first_vat.get("end_frame", 0.0))
        - float(first_vat.get("start_frame", 0.0)),
        1.0,
    )
    frame_advance = (
        float(second_vat.get("current_frame", 0.0))
        - float(first_vat.get("current_frame", 0.0))
    ) % frame_span
    population = second["population"]
    motion = second["motion"]
    ground = second["ground_and_clearance"]
    profile = second["profile"]
    telecom = second["telecom_regression"]

    checks = {
        "ground_only_300": (
            population["target"] == 300
            and population["admitted"] == 300
            and population["simulated"] == 300
            and population["represented"] == 300
            and ground["unsupported"] == 0
            and ground["invalid_positions"] == 0
            and ground["admission_violations"] == 0
        ),
        "long_out_and_back_motion": (
            motion["moving"] == motion["expected_moving"] == 300
            and motion["stuck"] == 0
            and motion["completed_path_legs"] > 0
            and float(profile.get("round_trip_m", 0.0)) >= 40.0
        ),
        "far_vat_animation": (
            bool(first_vat.get("valid"))
            and bool(second_vat.get("valid"))
            and bool(first_vat.get("animation_active"))
            and first_vat.get("stable_index") == second_vat.get("stable_index")
            and float(first_vat.get("distance_m", 0.0)) >= 40.0
            and frame_advance >= 1.0
        ),
        "click_profile_glass_ui": (
            bool(profile.get("valid"))
            and bool(profile.get("glass_panel_visible"))
            and bool(profile.get("person_id"))
            and bool(profile.get("name"))
            and bool(profile.get("occupation"))
            and bool(profile.get("favorite_software"))
        ),
        "no_severe_overlap": (
            ground["overlap_pairs"] == 0 and ground["overlap_agents"] == 0
        ),
        "telecom_scene_unchanged": (
            telecom["source_actor_count"] == 30
            and telecom["unique_source_label_count"] == 30
            and telecom["ray_actor_count"] == 1920
            and telecom["unique_ray_label_count"] == 1920
            and telecom["ray_geometry_count"] == 1920
            and telecom["color_geometry_counts"]
            == {"Green": 480, "Yellow": 480, "Orange": 480, "Red": 480}
        ),
    }
    report = {
        "schema": "telecomtwin-central-crowd-experience-runtime-v1",
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "sample_delay_seconds": args.sample_delay,
        "vat_frame_advance": round(frame_advance, 6),
        "first": first,
        "second": second,
        "checks": checks,
        "passed": all(checks.values()),
        "map_or_mass_entities_modified": False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
