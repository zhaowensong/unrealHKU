"""Verify the imported Ground-Only Central DataAsset inside Unreal Editor."""

from __future__ import annotations

import json
from pathlib import Path

import unreal


ASSET_PATH = "/Game/OpenMassCrowd/Central/DA_CentralNetwork_Certified"
REPORT_RELATIVE_PATH = Path(
    "Docs/Evidence/OpenMassCrowd/CentralCrowdExperience/ground_only_asset_latest.json"
)


def main():
    root = Path(unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_dir()))
    asset = unreal.load_asset(ASSET_PATH)
    if asset is None:
        # EditorAssetLibrary can reject package loads while PIE is active.  The
        # running spawner still holds the authoritative DataAsset reference, so
        # use that reference for a live-session verification.
        world = unreal.EditorLevelLibrary.get_game_world()
        if world is not None:
            spawners = unreal.GameplayStatics.get_all_actors_of_class(
                world, unreal.OpenMassCrowdSpawner
            )
            if spawners:
                asset = spawners[0].get_editor_property("central_network_asset")
    if asset is None:
        raise RuntimeError("Ground-Only Central asset is missing")
    cells = list(asset.get_editor_property("cells"))
    components = list(asset.get_editor_property("components"))
    districts = list(asset.get_editor_property("spawn_districts"))
    lanes = [
        lane
        for cell in cells
        for lane in list(cell.get_editor_property("directed_lanes"))
    ]
    ineligible = [
        str(lane.get_editor_property("lane_id"))
        for lane in lanes
        if not lane.get_editor_property("ground_only_eligible")
    ]
    population = sum(
        int(district.get_editor_property("target_population"))
        for district in districts
        if district.get_editor_property("enabled")
    )
    report = {
        "status": "ok",
        "asset_path": ASSET_PATH,
        "schema_version": int(asset.get_editor_property("schema_version")),
        "network_id": str(asset.get_editor_property("network_id")),
        "ground_only_network": bool(asset.get_editor_property("ground_only_network")),
        "parent_certified_sha256": str(
            asset.get_editor_property("parent_certified_sha256")
        ),
        "ground_only_policy_sha256": str(
            asset.get_editor_property("ground_only_policy_sha256")
        ),
        "excluded_source_feature_count": int(
            asset.get_editor_property("ground_only_excluded_source_feature_count")
        ),
        "cell_count": len(cells),
        "lane_count": len(lanes),
        "ground_only_eligible_lane_count": len(lanes) - len(ineligible),
        "ineligible_lane_ids": ineligible,
        "component_count": len(components),
        "spawn_district_count": len(districts),
        "target_population": population,
        "fail_closed_runtime_ready": (
            int(asset.get_editor_property("schema_version")) == 3
            and bool(asset.get_editor_property("ground_only_network"))
            and len(lanes) == 562
            and not ineligible
            and len(components) == 49
            and len(districts) == 6
            and population == 300
        ),
    }
    if not report["fail_closed_runtime_ready"]:
        raise RuntimeError("Ground-Only asset verification failed: " + json.dumps(report))
    output_path = root / REPORT_RELATIVE_PATH
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))


main()
