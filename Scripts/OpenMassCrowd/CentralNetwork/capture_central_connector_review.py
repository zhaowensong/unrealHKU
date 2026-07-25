"""Capture the current transient Central connector-review viewport.

Run immediately after ``review_central_connector_overlay.py``.  The script
does not move actors, save the level, or change connector trust.  It records
the exact overlay report beside a 1600x900 screenshot so a later manual review
decision can name the collision-certified source chain it inspected.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from datetime import datetime, timezone
from pathlib import Path

import unreal


OUTPUT_RELATIVE_PATH = Path(
    "Docs/Evidence/OpenMassCrowd/Central300/central_connector_review_latest.png"
)
REPORT_RELATIVE_PATH = Path(
    "Docs/Evidence/OpenMassCrowd/Central300/central_connector_review_latest.json"
)
OVERLAY_REPORT_RELATIVE_PATH = Path(
    "Saved/Reports/central_connector_review_overlay_latest.json"
)


def project_root() -> Path:
    return Path(unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_dir()))


def write_json_atomic(path: Path, document: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    for candidate in (path, temporary):
        if candidate.exists():
            os.chmod(candidate, stat.S_IREAD | stat.S_IWRITE)
    temporary.write_text(
        json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def main() -> None:
    root = project_root()
    overlay_path = root / OVERLAY_REPORT_RELATIVE_PATH
    overlay = json.loads(overlay_path.read_text(encoding="utf-8-sig"))
    if overlay.get("manual_only") is not True:
        raise RuntimeError("connector evidence requires a manual-only overlay")
    completed = overlay.get("completed_manual_source_ids") or []
    if not completed:
        raise RuntimeError("connector evidence requires a completed source chain")

    screenshot_path = root / OUTPUT_RELATIVE_PATH
    screenshot_path.parent.mkdir(parents=True, exist_ok=True)
    signal_rays = [
        actor
        for actor in unreal.EditorLevelLibrary.get_all_level_actors()
        if actor.get_actor_label().startswith("SIG_Ray_")
    ]
    hidden_signal_ray_count = sum(
        1 for actor in signal_rays if actor.is_temporarily_hidden_in_editor()
    )
    if hidden_signal_ray_count != len(signal_rays) or len(signal_rays) != 1920:
        raise RuntimeError(
            "expected 1920 temporarily hidden signal rays during connector review; "
            "found total={} hidden={}".format(
                len(signal_rays), hidden_signal_ray_count
            )
        )

    unreal.AutomationLibrary.take_high_res_screenshot(
        1600,
        900,
        str(screenshot_path),
        camera=None,
        mask_enabled=False,
        capture_hdr=False,
        delay=0.0,
        force_game_view=True,
    )
    # Editor-only review sessions have no PIE game viewport, so some UE 5.7
    # builds ignore AutomationLibrary's request.  The editor viewport console
    # path writes the same deterministic target and remains non-mutating.
    unreal.SystemLibrary.execute_console_command(
        unreal.EditorLevelLibrary.get_editor_world(),
        'HighResShot 1 filename="{}"'.format(
            str(screenshot_path).replace("\\", "/")
        ),
    )
    report = {
        "schema_version": 1,
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "world": overlay.get("world"),
        "level_saved": False,
        "connector_trust_changed": False,
        "signal_ray_count": len(signal_rays),
        "temporarily_hidden_signal_ray_count": hidden_signal_ray_count,
        "overlay_report": str(OVERLAY_REPORT_RELATIVE_PATH).replace("\\", "/"),
        "overlay_report_sha256": hashlib.sha256(overlay_path.read_bytes()).hexdigest(),
        "screenshot": str(OUTPUT_RELATIVE_PATH).replace("\\", "/"),
        "completed_manual_source_ids": completed,
        "review_candidate_count": len(overlay.get("review_candidate_ids") or []),
        "review_status": "pending-human-visual-review",
    }
    write_json_atomic(root / REPORT_RELATIVE_PATH, report)
    unreal.log_warning(
        "OPEN_MASS_CROWD_CENTRAL_CONNECTOR_SCREENSHOT_REQUESTED="
        + json.dumps(report, ensure_ascii=False, sort_keys=True)
    )


main()
