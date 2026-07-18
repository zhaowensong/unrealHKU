"""Audit the open Unreal project for genuine City Sample Crowds content.

Run through ``run_unreal_python_via_mcp.py`` while TelecomTwin is open.  The
script is deliberately read-only: it queries Asset Registry data and emits a
compact report that can be used to build the six final crowd variants.
"""

from __future__ import annotations

import json
from collections import Counter

import unreal


CITY_SAMPLE_MARKERS = (
    "citysample",
    "city_sample",
    "city sample",
)
MAX_PATHS_PER_CLASS = 40


def asset_class_name(asset_data):
    try:
        return str(asset_data.asset_class_path.asset_name)
    except AttributeError:
        return str(asset_data.asset_class_path).rsplit(".", 1)[-1]


def object_path(asset_data):
    return f"{asset_data.package_name}.{asset_data.asset_name}"


def is_city_sample_candidate(asset_data):
    searchable = " ".join(
        (
            str(asset_data.package_name),
            str(asset_data.package_path),
            str(asset_data.asset_name),
        )
    ).lower()
    return any(marker in searchable for marker in CITY_SAMPLE_MARKERS)


def main():
    registry = unreal.AssetRegistryHelpers.get_asset_registry()
    all_game_assets = registry.get_assets_by_path("/Game", recursive=True)
    candidates = [asset for asset in all_game_assets if is_city_sample_candidate(asset)]

    by_class = Counter(asset_class_name(asset) for asset in candidates)
    paths_by_class = {}
    for class_name in sorted(by_class):
        paths = sorted(
            object_path(asset)
            for asset in candidates
            if asset_class_name(asset) == class_name
        )
        paths_by_class[class_name] = paths[:MAX_PATHS_PER_CLASS]

    editor_subsystem = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem)
    editor_world = editor_subsystem.get_editor_world()
    report = {
        "city_sample_found": bool(candidates),
        "candidate_asset_count": len(candidates),
        "candidate_class_counts": dict(sorted(by_class.items())),
        "candidate_paths_by_class": paths_by_class,
        "editor_world": editor_world.get_name() if editor_world else None,
        "game_asset_count": len(all_game_assets),
    }
    print("OPEN_MASS_CITY_SAMPLE_AUDIT=" + json.dumps(report, ensure_ascii=False))


main()
