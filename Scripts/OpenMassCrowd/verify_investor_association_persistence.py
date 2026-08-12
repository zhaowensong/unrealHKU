#!/usr/bin/env python3
"""Prove that every current person/station link stays rendered over time."""

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
    / "person_station_association_persistence_latest.json"
)
MARKER = "INVESTOR_ASSOCIATION_PERSISTENCE="


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
delivery = json.loads(spawners[0].get_investor_demo_evidence_snapshot())
print({MARKER!r} + json.dumps({{
    "world": world.get_path_name(),
    "network": delivery["network"],
    "population": delivery["population"],
    "liveness": delivery["liveness"],
    "performance": delivery["performance"],
}}, ensure_ascii=False, sort_keys=True))
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
    parser.add_argument("--samples", type=int, default=12)
    parser.add_argument("--interval", type=float, default=2.0)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if args.samples < 2 or args.interval <= 0.0:
        raise ValueError("samples must be >= 2 and interval must be positive")

    samples: list[dict[str, Any]] = []
    for index in range(args.samples):
        item = snapshot()
        network = item["network"]
        samples.append(
            {
                "sample": index,
                "elapsed_s": round(index * args.interval, 3),
                "connected": int(network["connected"]),
                "source_connected": int(
                    network["association_source_connected"]
                ),
                "rendered_links": int(network["association_rendered_links"]),
                "rendered_dashed_links": int(
                    network["association_rendered_dashed_links"]
                ),
                "rendered_segments": int(
                    network["association_rendered_segments"]
                ),
                "revision": int(network["association_visual_revision"]),
                "full_coverage": bool(network["association_full_coverage"]),
                "policy": str(network["association_visual_policy"]),
                "rotating_sampling": bool(network["rotating_sampling"]),
                "persistent_batch_component": bool(
                    network["persistent_batch_component"]
                ),
                "persistent_batch_component_tick": bool(
                    network["persistent_batch_component_tick"]
                ),
                "single_batch_refresh": bool(network["single_batch_refresh"]),
                "moving": int(item["liveness"]["moving"]),
                "stuck": int(item["liveness"]["stuck"]),
                "frame_p95_ms": float(item["performance"]["frame_p95_ms"]),
            }
        )
        if index + 1 < args.samples:
            time.sleep(args.interval)

    revisions = [item["revision"] for item in samples]
    checks = {
        "all_samples_full_coverage": all(
            item["full_coverage"] for item in samples
        ),
        "all_connected_people_rendered": all(
            item["connected"]
            == item["source_connected"]
            == item["rendered_links"]
            for item in samples
        ),
        "no_rotating_sampling": all(
            not item["rotating_sampling"] for item in samples
        ),
        "persistent_batch_component": all(
            item["persistent_batch_component"] for item in samples
        ),
        "persistent_batch_tick_disabled": all(
            not item["persistent_batch_component_tick"] for item in samples
        ),
        "single_batch_refresh": all(
            item["single_batch_refresh"] for item in samples
        ),
        "persistent_policy": all(
            item["policy"] == "all_connected_people_persistent_batch"
            for item in samples
        ),
        "visual_refresh_continued": all(
            current > previous
            for previous, current in zip(revisions, revisions[1:])
        ),
        "at_least_95_connected": min(
            item["connected"] for item in samples
        )
        >= 95,
        "no_stuck_people": all(item["stuck"] == 0 for item in samples),
    }
    report = {
        "schema": "telecomtwin-person-station-association-persistence-v1",
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "duration_s": round((args.samples - 1) * args.interval, 3),
        "sample_count": args.samples,
        "checks": checks,
        "passed": all(checks.values()),
        "samples": samples,
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
