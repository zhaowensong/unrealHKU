#!/usr/bin/env python3
"""Verify the investor delivery loop against live PIE without editing the map."""

from __future__ import annotations

import argparse
import json
import math
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
    / "investor_delivery_runtime_latest.json"
)
MARKER = "INVESTOR_DELIVERY_RUNTIME="


def snapshot(profile_index: int = 0) -> dict[str, Any]:
    code = f"""
import json
import unreal

world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_game_world()
if world is None or "UEDPIE_" not in world.get_path_name().upper():
    raise RuntimeError("active PIE world required")
spawners = unreal.GameplayStatics.get_all_actors_of_class(
    world, unreal.OpenMassCrowdSpawner
)
if len(spawners) != 1:
    raise RuntimeError("expected one OpenMassCrowdSpawner, found {{}}".format(len(spawners)))
spawner = spawners[0]
spawner.show_central_profile_by_stable_index({int(profile_index)})
payload = {{
    "world": world.get_path_name(),
    "delivery": json.loads(spawner.get_investor_demo_evidence_snapshot()),
    "profile": json.loads(spawner.get_central_profile_evidence_snapshot()),
    "vat": json.loads(spawner.get_central_vat_animation_evidence_snapshot()),
    "ground": {{
        "unsupported": int(spawner.get_current_unsupported_visual_count()),
        "invalid_positions": int(spawner.get_central_invalid_position_observation_count()),
        "overlap_pairs": int(spawner.get_central_severe_overlap_pair_count()),
        "overlap_agents": int(spawner.get_central_severe_overlap_agent_count()),
    }},
}}
print({MARKER!r} + json.dumps(payload, ensure_ascii=False, sort_keys=True))
"""
    response = execute_python(code, timeout_seconds=30.0)
    if response.get("status") != "success":
        raise RuntimeError(response.get("message") or "runtime snapshot failed")
    output = str((response.get("result") or {}).get("output") or "")
    for line in reversed(output.splitlines()):
        if line.startswith(MARKER):
            return json.loads(line[len(MARKER) :])
    raise RuntimeError("snapshot marker missing: {!r}".format(output))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--transition-wait", type=float, default=14.0)
    parser.add_argument("--expected-population", type=int)
    args = parser.parse_args()

    configured_population = int(
        json.loads(
            (ROOT / "Config" / "InvestorDeliveryDemo.json").read_text(
                encoding="utf-8"
            )
        )["population"]
    )
    expected_population = args.expected_population or configured_population
    if expected_population <= 0:
        raise ValueError("expected population must be positive")

    first = snapshot()
    time.sleep(max(args.transition_wait, 1.0))
    second = snapshot()
    delivery = second["delivery"]
    population = delivery["population"]
    stations = delivery["stations"]
    building = delivery["building"]
    presentation = delivery["presentation"]
    performance = delivery["performance"]
    liveness = delivery["liveness"]
    signal_rendering = delivery["signal_rendering"]
    profile = second["profile"]
    vat = second["vat"]
    ground = second["ground"]
    checks = {
        f"healthy_{expected_population}_ground_route_people": (
            delivery["schema"] == "telecomtwin-investor-delivery-v3"
            and liveness["expected_moving"] == expected_population
            and liveness["moving"] >= math.ceil(expected_population * 0.95)
            and liveness["stuck"] == 0
            and population["configured"] == expected_population
            and population["spawned"] == expected_population
            and population["admitted"] == expected_population
            and population["moving"] >= math.ceil(expected_population * 0.95)
            and population["represented"] == expected_population
            and ground["unsupported"] == 0
            and ground["invalid_positions"] == 0
        ),
        "far_walk_animation_active": (
            bool(vat.get("valid"))
            and bool(vat.get("animation_active"))
            and float(vat.get("distance_m", 0.0)) >= 60.0
            and float(vat.get("play_rate", 0.0)) > 0.0
        ),
        "crowd_spread_across_supported_bands": (
            int(presentation["configured_bands"]) == 7
            and int(presentation["occupied_supported_bands"]) >= 5
            and int(presentation["offset_supported_people"]) >= 35
            and float(presentation["maximum_lateral_offset_cm"]) == 135.0
            and int(presentation["unique_active_lanes"]) >= 3
        ),
        "bounded_distant_skeletal_walk": (
            float(presentation["skeletal_walk_distance_m"]) >= 200.0
            and int(presentation["high_actor_budget"]) == 6
            and int(presentation["low_actor_budget"]) == 24
            and int(presentation["high_actors"])
            <= int(presentation["high_actor_budget"])
            and int(presentation["low_actors"])
            <= int(presentation["low_actor_budget"])
            and int(presentation["vat_actors"]) > 0
        ),
        "bounded_investor_runtime_work": (
            int(performance["ground_guards_per_pass"]) == 4
            and float(performance["telemetry_interval_s"]) >= 1.0
            and float(performance["debug_refresh_hz"]) <= 10.0
            and float(performance["validated_roof_refresh_s"]) >= 2.0
            and int(performance["frame_samples"]) > 0
            and float(performance["frame_p50_ms"]) > 0.0
            and float(performance["frame_p95_ms"]) > 0.0
            and float(performance["frame_p95_ms"]) < 33.0
        ),
        "two_real_rooftop_stations": (
            stations["validated"] == stations["required"] == 2
            and all(item["roof_validated"] for item in stations["items"])
            and float(stations["maximum_roof_error_cm"]) <= 4.01
            and all(
                float(item["station_to_live_roof_offset_cm"]) <= 4.01
                and int(item["consecutive_validation_misses"]) == 0
                for item in stations["items"]
            )
        ),
        "live_person_station_association": (
            delivery["network"]["connected"] > 0
            and delivery["network"]["association_visual_budget"] == 12
            and delivery["network"]["association_visual_enabled"]
            and delivery["network"]["selected_link_style"] == "solid_blue"
            and delivery["network"]["other_link_style"] == "dashed_gray"
            and float(delivery["network"]["station_endpoint_offset_cm"])
            <= 4.01
        ),
        "building_disconnect_and_reacquire": (
            building["portal_grounded"]
            and building["entry_events"] > 0
            and building["exit_events"] > 0
            and building["station_reacquisitions"] > 0
        ),
        "investor_profile_complete": (
            profile.get("valid")
            and profile.get("glass_panel_visible")
            and profile.get("panel_anchor") == "lower_left"
            and bool(profile.get("name"))
            and bool(profile.get("occupation"))
            and bool(profile.get("gender"))
            and int(profile.get("age", 0)) > 0
            and bool(profile.get("current_app"))
            and bool(profile.get("location_state"))
            and bool(profile.get("serving_station"))
            and bool(profile.get("signal_quality"))
        ),
        "no_severe_overlap": (
            ground["overlap_pairs"] == 0 and ground["overlap_agents"] == 0
        ),
        "persisted_rooftop_signal_untouched_during_pie": (
            signal_rendering["source"]
            == "persisted_editor_actor_components"
            and signal_rendering["parity_ready"]
            and int(signal_rendering["original_actor_count"]) == 1950
            and int(signal_rendering["original_visible_actor_count"]) == 1950
            and int(signal_rendering["source_actor_count"]) == 30
            and int(signal_rendering["ray_actor_count"]) == 1920
            and not signal_rendering["transforms_modified"]
            and not signal_rendering["actor_visibility_modified"]
            and not signal_rendering["runtime_rebuild_enabled"]
            and not signal_rendering["runtime_overlay_enabled"]
            and delivery["legacy_signal"]["preserved_visible"]
            and not delivery["legacy_signal"]["runtime_overlay_enabled"]
            and int(delivery["legacy_signal"]["floating_mock_visible_count"])
            == 0
            and float(delivery["legacy_signal"]["late_stream_scan_hz"])
            >= 4.0
        ),
        "video_excluded": delivery["video_required"] is False,
    }
    report = {
        "schema": "telecomtwin-investor-delivery-acceptance-v3",
        "baseline_frame_p50_ms": 333.3336,
        "expected_population": expected_population,
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "checks": checks,
        "passed": all(checks.values()),
        "first": first,
        "second": second,
        "map_modified": False,
        "acceptance_video_created": False,
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
