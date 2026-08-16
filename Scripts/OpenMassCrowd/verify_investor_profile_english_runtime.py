#!/usr/bin/env python3
"""Verify that every live investor person profile is rendered in English."""

from __future__ import annotations

import argparse
import json
import re
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
    / "investor_profile_english_runtime_2026-08-16.json"
)
DEFAULT_SCREENSHOT = (
    ROOT
    / "Docs"
    / "Evidence"
    / "InvestorDelivery"
    / "18_english_person_profile_window_2026-08-16.jpg"
)
MARKER = "INVESTOR_ENGLISH_PROFILES="
CJK_PATTERN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
DISPLAY_FIELDS = (
    "name",
    "occupation",
    "gender",
    "favorite_software",
    "current_app",
    "location_state",
    "serving_station",
    "signal_quality",
)


def collect_profiles(selected_index: int) -> dict[str, Any]:
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
profiles = []
for stable_index in range(int(spawner.get_spawned_entity_count())):
    if not spawner.show_central_profile_by_stable_index(stable_index):
        raise RuntimeError("unable to show profile {{}}".format(stable_index))
    profiles.append(json.loads(spawner.get_central_profile_evidence_snapshot()))
spawner.show_central_profile_by_stable_index({int(selected_index)})
print({MARKER!r} + json.dumps({{
    "world": world.get_path_name(),
    "profiles": profiles,
    "selected_index": {int(selected_index)},
}}, ensure_ascii=False, sort_keys=True))
"""
    response = execute_python(code, timeout_seconds=60.0)
    if response.get("status") != "success":
        raise RuntimeError(response.get("message") or "profile collection failed")
    output = str((response.get("result") or {}).get("output") or "")
    for line in reversed(output.splitlines()):
        if line.startswith(MARKER):
            return json.loads(line[len(MARKER) :])
    raise RuntimeError("profile marker missing: {!r}".format(output))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--screenshot", type=Path, default=DEFAULT_SCREENSHOT)
    parser.add_argument("--selected-index", type=int, default=5)
    args = parser.parse_args()

    payload = collect_profiles(args.selected_index)
    profiles = payload["profiles"]
    violations: list[dict[str, Any]] = []
    for profile in profiles:
        field_violations = [
            field
            for field in DISPLAY_FIELDS
            if not isinstance(profile.get(field), str)
            or not profile[field].strip()
            or CJK_PATTERN.search(profile[field]) is not None
        ]
        if (
            profile.get("ui_language") != "en"
            or profile.get("display_text_english_only") is not True
            or field_violations
        ):
            violations.append(
                {
                    "stable_index": profile.get("stable_index"),
                    "person_id": profile.get("person_id"),
                    "invalid_fields": field_violations,
                    "ui_language": profile.get("ui_language"),
                    "display_text_english_only": profile.get(
                        "display_text_english_only"
                    ),
                }
            )

    source_path = (
        ROOT
        / "Plugins"
        / "OpenMassCrowd"
        / "Source"
        / "OpenMassCrowd"
        / "Private"
        / "OpenMassCrowdSpawner.cpp"
    )
    source_cjk = CJK_PATTERN.findall(source_path.read_text(encoding="utf-8"))
    names = [str(profile.get("name", "")) for profile in profiles]
    report = {
        "schema": "telecomtwin-investor-profile-language-v1",
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "world": payload["world"],
        "ui_language": "en",
        "profile_count": len(profiles),
        "unique_name_count": len(set(names)),
        "selected_index": payload["selected_index"],
        "window_screenshot": str(args.screenshot.resolve()),
        "window_screenshot_exists": args.screenshot.is_file(),
        "source_cjk_character_count": len(source_cjk),
        "violations": violations,
        "sample_profiles": profiles[:10],
        "passed": (
            bool(profiles)
            and len(set(names)) == len(names)
            and args.screenshot.is_file()
            and not source_cjk
            and not violations
        ),
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
