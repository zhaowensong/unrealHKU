#!/usr/bin/env python3
"""Capture two fixed-camera PIE frames of the same far VAT pedestrian."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from run_unreal_python_via_mcp import execute_python


ROOT = Path(__file__).resolve().parents[2]
EVIDENCE_DIR = ROOT / "Docs" / "Evidence" / "OpenMassCrowd" / "CentralCrowdExperience"
FIRST_PNG = EVIDENCE_DIR / "12_central_100_far_gait_a.png"
SECOND_PNG = EVIDENCE_DIR / "13_central_100_far_gait_b.png"
REPORT_JSON = EVIDENCE_DIR / "far_gait_pair_latest.json"
MARKER = "OPEN_MASS_CROWD_FAR_GAIT="


def run_unreal(code: str) -> str:
    response = execute_python(code, timeout_seconds=30.0)
    if response.get("status") != "success":
        raise RuntimeError(response.get("message") or "Unreal command failed")
    return str((response.get("result") or {}).get("output") or "")


def request_capture(path: Path, stable_index: int | None) -> dict[str, Any]:
    safe_path = str(path.resolve()).replace("\\", "/")
    requested_index = "None" if stable_index is None else str(int(stable_index))
    output = run_unreal(
        f"""
import builtins
import json
import unreal

world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_game_world()
if world is None or "UEDPIE_" not in world.get_path_name().upper():
    raise RuntimeError("an active PIE world is required")
spawners = unreal.GameplayStatics.get_all_actors_of_class(
    world, unreal.OpenMassCrowdSpawner
)
if len(spawners) != 1:
    raise RuntimeError("expected exactly one OpenMassCrowdSpawner")
spawner = spawners[0]
camera_state = getattr(builtins, "_hk_central_runtime_lod_camera_state", None)
if not isinstance(camera_state, dict):
    raise RuntimeError("run set_central_runtime_lod_camera.py --tier far first")
stable_index = {requested_index}
if stable_index is None:
    anchor = camera_state["target_snapshot"]["location"]
    candidates = []
    for candidate_index in range(100):
        candidate = json.loads(
            spawner.get_central_vat_animation_evidence_snapshot_for_stable_index(
                candidate_index
            )
        )
        if not candidate.get("valid") or not candidate.get("animation_active"):
            continue
        if float(candidate.get("speed_cm_s", 0.0)) < 50.0:
            continue
        point = candidate["location"]
        anchor_distance_squared = sum(
            (float(point[key]) - float(anchor[key])) ** 2
            for key in ("x", "y", "z")
        )
        candidates.append((anchor_distance_squared, candidate_index, candidate))
    if not candidates:
        raise RuntimeError("no moving VAT target is available near the evidence view")
    candidates.sort(key=lambda item: item[0])
    stable_index = int(candidates[0][1])
    camera_state["target_stable_index"] = stable_index
    camera_state["target_person_id"] = str(candidates[0][2]["person_id"])
    camera_state["target_snapshot"] = candidates[0][2]
snapshot = json.loads(
    spawner.get_central_vat_animation_evidence_snapshot_for_stable_index(
        int(stable_index)
    )
)
if not snapshot.get("valid"):
    raise RuntimeError("fixed VAT target is unavailable: {{}}".format(snapshot))
unreal.AutomationLibrary.take_high_res_screenshot(
    1600,
    900,
    {safe_path!r},
    camera=None,
    mask_enabled=False,
    capture_hdr=False,
    delay=0.0,
    force_game_view=True,
)
print({MARKER!r} + json.dumps({{
    "snapshot": snapshot,
    "camera_target_stable_index": int(camera_state["target_stable_index"]),
    "camera_target_person_id": str(camera_state["target_person_id"]),
    "screenshot": {safe_path!r},
}}, ensure_ascii=False, sort_keys=True))
"""
    )
    for line in reversed(output.splitlines()):
        if line.startswith(MARKER):
            return json.loads(line[len(MARKER) :])
    raise RuntimeError(f"capture marker missing: {output!r}")


def wait_for_png(path: Path, timeout_seconds: float = 15.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_size = -1
    stable_since = 0.0
    while time.monotonic() < deadline:
        if path.is_file():
            size = path.stat().st_size
            if size > 0 and size == last_size:
                if stable_since and time.monotonic() - stable_since >= 0.3:
                    return
            else:
                last_size = size
                stable_since = time.monotonic()
        time.sleep(0.1)
    raise RuntimeError(f"screenshot did not stabilize: {path}")


def wait_for_world_advance(
    stable_index: int,
    start_world_time: float,
    minimum_seconds: float = 0.75,
    timeout_seconds: float = 10.0,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        output = run_unreal(
            f"""
import json
import unreal
world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_game_world()
unreal.GameplayStatics.set_game_paused(world, False)
spawner = unreal.GameplayStatics.get_all_actors_of_class(
    world, unreal.OpenMassCrowdSpawner
)[0]
snapshot = json.loads(
    spawner.get_central_vat_animation_evidence_snapshot_for_stable_index(
        {int(stable_index)}
    )
)
print({MARKER!r} + json.dumps(snapshot, sort_keys=True))
"""
        )
        snapshot = None
        for line in reversed(output.splitlines()):
            if line.startswith(MARKER):
                snapshot = json.loads(line[len(MARKER) :])
                break
        if snapshot and (
            float(snapshot.get("world_time_seconds", 0.0)) - start_world_time
            >= minimum_seconds
        ):
            return
        time.sleep(0.1)
    raise RuntimeError("PIE world time did not advance between gait captures")


def main() -> int:
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    for path in (FIRST_PNG, SECOND_PNG):
        if path.exists():
            path.unlink()

    first = second = None
    frame_advance = 0.0
    for _attempt in range(4):
        for path in (FIRST_PNG, SECOND_PNG):
            if path.exists():
                path.unlink()
        first = request_capture(FIRST_PNG, None)
        wait_for_png(FIRST_PNG)
        stable_index = int(first["snapshot"]["stable_index"])
        wait_for_world_advance(
            stable_index,
            float(first["snapshot"]["world_time_seconds"]),
        )
        second = request_capture(SECOND_PNG, stable_index)
        wait_for_png(SECOND_PNG)
        start = first["snapshot"]
        end = second["snapshot"]
        frame_span = max(
            float(start["end_frame"]) - float(start["start_frame"]), 1.0
        )
        frame_advance = (
            float(end["current_frame"]) - float(start["current_frame"])
        ) % frame_span
        if (
            bool(start["animation_active"])
            and bool(end["animation_active"])
            and frame_advance >= 1.0
        ):
            break
    assert first is not None and second is not None
    start = first["snapshot"]
    end = second["snapshot"]
    report = {
        "schema": "telecomtwin-central-100-far-gait-pair-v1",
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "camera_distance_gate_m": 60.0,
        "camera_fov_degrees": 35.0,
        "first": first,
        "second": second,
        "same_stable_person": (
            start["stable_index"] == end["stable_index"]
            and start["person_id"] == end["person_id"]
        ),
        "frame_advance": round(frame_advance, 6),
        "passed": (
            start["stable_index"] == end["stable_index"]
            and bool(start["animation_active"])
            and bool(end["animation_active"])
            and float(start["distance_m"]) >= 60.0
            and float(end["distance_m"]) >= 60.0
            and frame_advance >= 1.0
        ),
        "map_or_mass_entities_modified": False,
    }
    REPORT_JSON.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
