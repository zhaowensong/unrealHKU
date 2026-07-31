#!/usr/bin/env python3
"""Verify that expected-moving investor pedestrians do not remain stalled."""

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
    / "investor_long_run_liveness_latest.json"
)
MARKER = "INVESTOR_LONG_RUN_SAMPLE="


def snapshot() -> dict[str, Any]:
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
    raise RuntimeError("expected one OpenMassCrowdSpawner")
spawner = spawners[0]
delivery = json.loads(spawner.get_investor_demo_evidence_snapshot())
payload = {{
    "world": world.get_path_name(),
    "world_time_s": unreal.GameplayStatics.get_time_seconds(world),
    "paused": unreal.GameplayStatics.is_game_paused(world),
    "delivery": delivery,
    "unsupported": int(spawner.get_current_unsupported_visual_count()),
}}
print({MARKER!r} + json.dumps(payload, ensure_ascii=False, sort_keys=True))
"""
    response = execute_python(code, timeout_seconds=30.0)
    if response.get("status") != "success":
        raise RuntimeError(response.get("message") or "liveness snapshot failed")
    output = str((response.get("result") or {}).get("output") or "")
    for line in reversed(output.splitlines()):
        if line.startswith(MARKER):
            return json.loads(line[len(MARKER) :])
    raise RuntimeError("liveness marker missing: {!r}".format(output))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duration", type=float, default=60.0)
    parser.add_argument("--interval", type=float, default=2.0)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    duration = max(args.duration, 10.0)
    interval = max(args.interval, 1.0)
    deadline = time.monotonic() + duration
    samples: list[dict[str, Any]] = []
    while True:
        samples.append(snapshot())
        if time.monotonic() >= deadline:
            break
        time.sleep(interval)

    longest_stuck_streak = 0
    current_stuck_streak = 0
    minimum_moving = 50
    maximum_stuck = 0
    maximum_stationary_s = 0.0
    for sample in samples:
        delivery = sample["delivery"]
        liveness = delivery["liveness"]
        stuck = int(liveness["stuck"])
        minimum_moving = min(minimum_moving, int(liveness["moving"]))
        maximum_stuck = max(maximum_stuck, stuck)
        maximum_stationary_s = max(
            maximum_stationary_s,
            float(liveness["maximum_stationary_s"]),
        )
        current_stuck_streak = current_stuck_streak + 1 if stuck else 0
        longest_stuck_streak = max(longest_stuck_streak, current_stuck_streak)

    final_delivery = samples[-1]["delivery"]
    final_liveness = final_delivery["liveness"]
    checks = {
        "active_unpaused_pie": all(not sample["paused"] for sample in samples),
        "v3_liveness_evidence": all(
            sample["delivery"]["schema"]
            == "telecomtwin-investor-delivery-v3"
            for sample in samples
        ),
        "population_remains_admitted": all(
            sample["delivery"]["population"]["spawned"] == 50
            and sample["delivery"]["population"]["admitted"] == 50
            and sample["delivery"]["population"]["represented"] == 50
            and sample["delivery"]["liveness"]["expected_moving"] == 50
            for sample in samples
        ),
        "no_unsupported_positions": all(
            sample["unsupported"] == 0 for sample in samples
        ),
        "no_persistent_stall": (
            longest_stuck_streak <= 2
            and int(final_liveness["stuck"]) == 0
            and minimum_moving >= 45
            and maximum_stationary_s < 15.0
        ),
    }
    report = {
        "schema": "telecomtwin-investor-long-run-liveness-v1",
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "duration_s": duration,
        "interval_s": interval,
        "sample_count": len(samples),
        "minimum_moving": minimum_moving,
        "maximum_stuck": maximum_stuck,
        "maximum_stationary_s": maximum_stationary_s,
        "longest_stuck_sample_streak": longest_stuck_streak,
        "stall_recovery_replans": int(final_liveness["stall_recovery_replans"]),
        "checks": checks,
        "passed": all(checks.values()),
        "samples": samples,
        "map_modified": False,
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
