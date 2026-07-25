"""Persist the staged Central crowd configuration in ``/Game/Maps/shanghai``.

Run through ``run_unreal_python_via_mcp.py`` after the certified Central data
asset and modular City Sample VAT assets have passed their strict verifiers.
Run only with PIE stopped. The level is deliberately saved at Gate30; use
``promote_central_crowd_gate.py`` to persist each later gate after collecting
the previous gate's passing runtime report.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from datetime import datetime, timezone
from pathlib import Path

import unreal


MAP_NAME = "shanghai"
MAP_OBJECT_PATH = "/Game/Maps/shanghai.shanghai"
SPAWNER_LABEL = "HK_Central_300_Crowd_Spawner"
NETWORK_ASSET_PATH = "/Game/OpenMassCrowd/Central/DA_CentralNetwork_Certified"
MANIFEST_RELATIVE_PATH = Path(
    "Scripts/OpenMassCrowd/city_sample_vat_manifest.json"
)
REPORT_BASENAME = "central_crowd_setup_latest.json"
EXPECTED_VARIANTS = ("FTN", "FTO", "FTU", "MTN", "MTO", "MTU")
EXPECTED_PARTS = ("Torso", "Legs", "Shoes", "Head")


def project_root() -> Path:
    return Path(
        unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_dir())
    )


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require_type(name: str):
    value = getattr(unreal, name, None)
    if value is None:
        raise RuntimeError(
            "reflected type {} is unavailable; rebuild OpenMassCrowd and "
            "restart Unreal Editor".format(name)
        )
    return value


def load_asset(path: str):
    asset = unreal.EditorAssetLibrary.load_asset(path)
    if asset is None:
        raise RuntimeError("required asset is missing: {}".format(path))
    return asset


def output_path(root: str, variant_id: str, part_id: str, prefix: str) -> str:
    name = "{}_CitySample_{}_{}_VAT".format(prefix, variant_id, part_id)
    return "{}/{}/{}.{}".format(root, variant_id, name, name)


def build_visual_variants(manifest):
    visual_type = require_type("OpenMassCrowdVisualConfig")
    part_type = require_type("OpenMassCrowdVATPartConfig")
    high_class = unreal.load_class(
        None, "/Script/OpenMassCrowd.OpenMassCrowdCitySampleActor"
    )
    low_class = unreal.load_class(
        None, "/Script/OpenMassCrowd.OpenMassCrowdCitySampleLowResActor"
    )
    if high_class is None or low_class is None:
        raise RuntimeError("official City Sample Mass actor classes are unavailable")

    root = str(manifest["output_root"])
    by_id = {str(item["id"]): item for item in manifest["variants"]}
    if tuple(sorted(by_id)) != tuple(sorted(EXPECTED_VARIANTS)):
        raise RuntimeError("manifest must contain exactly six official variants")

    visuals = []
    asset_paths = []
    for variant_id in EXPECTED_VARIANTS:
        source = by_id[variant_id]
        source_parts = {str(item["id"]): item for item in source["parts"]}
        if tuple(sorted(source_parts)) != tuple(sorted(EXPECTED_PARTS)):
            raise RuntimeError(
                "variant {} must contain exactly {}".format(
                    variant_id, ", ".join(EXPECTED_PARTS)
                )
            )

        parts = []
        for part_id in EXPECTED_PARTS:
            part_source = source_parts[part_id]
            mesh_path = output_path(root, variant_id, part_id, "SM")
            data_path = output_path(root, variant_id, part_id, "DA")
            animation_path = str(
                part_source.get("animation", source["animation"])
            )
            part = part_type()
            part.set_editor_property("part_name", unreal.Name(part_id))
            part.set_editor_property("static_mesh", load_asset(mesh_path))
            part.set_editor_property("animation_data", load_asset(data_path))
            part.set_editor_property(
                "animation_sequence", load_asset(animation_path)
            )
            part.set_editor_property("local_transform", unreal.Transform())
            part.set_editor_property("cast_shadows", True)
            parts.append(part)
            asset_paths.extend((mesh_path, data_path, animation_path))

        visual = visual_type()
        visual.set_editor_property(
            "variant_name", unreal.Name("CitySample_{}".format(variant_id))
        )
        visual.set_editor_property("use_actor_representation", True)
        visual.set_editor_property("high_res_template_actor", high_class)
        visual.set_editor_property("low_res_template_actor", low_class)
        visual.set_editor_property("vat_parts", parts)
        visual.set_editor_property("local_transform", unreal.Transform())
        visual.set_editor_property("cast_shadows", True)
        visuals.append(visual)
    return visuals, sorted(set(asset_paths))


def find_or_create_single_spawner():
    actor_subsystem = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    spawner_class = require_type("OpenMassCrowdSpawner")
    spawners = [
        actor
        for actor in actor_subsystem.get_all_level_actors()
        if isinstance(actor, spawner_class)
    ]
    spawners.sort(key=lambda actor: actor.get_path_name())
    if spawners:
        selected = spawners[0]
        for duplicate in spawners[1:]:
            duplicate_path = duplicate.get_path_name()
            if not actor_subsystem.destroy_actor(duplicate):
                raise RuntimeError(
                    "failed to remove duplicate Central crowd spawner: {}".format(
                        duplicate_path
                    )
                )
    else:
        selected = actor_subsystem.spawn_actor_from_class(
            spawner_class, unreal.Vector(0.0, 0.0, 0.0)
        )
    if selected is None:
        raise RuntimeError("failed to create the Central crowd spawner")
    selected.set_actor_label(SPAWNER_LABEL)
    return selected, len(spawners)


def require_single_spawner(selected):
    actor_subsystem = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    spawner_class = require_type("OpenMassCrowdSpawner")
    spawners = [
        actor
        for actor in actor_subsystem.get_all_level_actors()
        if isinstance(actor, spawner_class)
    ]
    selected_path = selected.get_path_name()
    actual_paths = sorted(actor.get_path_name() for actor in spawners)
    if len(spawners) != 1 or actual_paths[0] != selected_path:
        raise RuntimeError(
            "Central setup requires exactly the selected spawner; selected={} "
            "actual={}".format(selected_path, actual_paths)
        )
    return len(spawners)


def enum_value(type_name: str, value_name: str):
    enum_type = require_type(type_name)
    value = getattr(enum_type, value_name, None)
    if value is None:
        raise RuntimeError("enum {}.{} is unavailable".format(type_name, value_name))
    return value


def require_pie_stopped():
    subsystem_type = getattr(unreal, "UnrealEditorSubsystem", None)
    if subsystem_type is None:
        raise RuntimeError("cannot prove PIE is stopped: UnrealEditorSubsystem is absent")
    subsystem = unreal.get_editor_subsystem(subsystem_type)
    getter = getattr(subsystem, "get_game_world", None)
    if subsystem is None or getter is None:
        raise RuntimeError("cannot prove PIE is stopped: get_game_world is unavailable")
    game_world = getter()
    if game_world is not None:
        raise RuntimeError(
            "PIE or Simulate is active (game world={}); stop it before Central "
            "setup".format(game_world.get_path_name())
        )


def ensure_map_file_writable() -> tuple[Path, bool]:
    """Clear only a OneDrive-restored read-only bit before saving shanghai."""
    map_file = Path(
        unreal.Paths.convert_relative_path_to_full(
            unreal.Paths.project_content_dir()
        )
    ) / "Maps" / "shanghai.umap"
    if not map_file.is_file():
        raise RuntimeError("shanghai map package is missing: {}".format(map_file))
    was_read_only = not (map_file.stat().st_mode & stat.S_IWRITE)
    if was_read_only:
        os.chmod(map_file, map_file.stat().st_mode | stat.S_IWRITE)
    return map_file, was_read_only


def atomic_write_text(path: Path, text: str):
    temporary = path.with_suffix(path.suffix + ".tmp")
    for candidate in (path, temporary):
        if candidate.exists() and not (candidate.stat().st_mode & stat.S_IWRITE):
            os.chmod(candidate, candidate.stat().st_mode | stat.S_IWRITE)
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def write_report(payload):
    report_dir = Path(
        unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_saved_dir())
    ) / "Reports" / "OpenMassCrowd"
    report_dir.mkdir(parents=True, exist_ok=True)
    latest = report_dir / REPORT_BASENAME
    timestamped = report_dir / (
        "central_crowd_setup_{}.json".format(
            datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        )
    )
    text = (
        json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    atomic_write_text(latest, text)
    atomic_write_text(timestamped, text)
    return latest, timestamped


def main():
    require_pie_stopped()
    world = unreal.EditorLevelLibrary.get_editor_world()
    world_path = world.get_path_name() if world is not None else None
    if world_path != MAP_OBJECT_PATH:
        raise RuntimeError(
            "open {} before Central setup; current={}".format(
                MAP_OBJECT_PATH, world_path
            )
        )
    root = project_root()
    manifest_path = root / MANIFEST_RELATIVE_PATH
    manifest = load_json(manifest_path)
    if int(manifest.get("schema_version", 0)) != 2:
        raise RuntimeError("City Sample VAT manifest schema_version must be 2")
    visuals, asset_paths = build_visual_variants(manifest)
    network_asset = load_asset(NETWORK_ASSET_PATH)
    map_file, map_read_only_cleared = ensure_map_file_writable()

    with unreal.ScopedEditorTransaction("Configure Central 300 Mass crowd"):
        spawner, previous_spawner_count = find_or_create_single_spawner()
        spawner.set_editor_property(
            "network_mode",
            enum_value(
                "OpenMassCrowdNetworkMode", "CENTRAL_CERTIFIED_CACHE"
            ),
        )
        spawner.set_editor_property("central_network_asset", network_asset)
        spawner.set_editor_property(
            "central_population_gate",
            enum_value("OpenMassCrowdCentralPopulationGate", "GATE30"),
        )
        spawner.set_editor_property("population_count", 30)
        spawner.set_editor_property("central_admission_batch_size", 25)
        spawner.set_editor_property("central_admission_batch_interval", 0.1)
        spawner.set_editor_property("draw_central_network_overlay", False)
        spawner.set_editor_property("spawn_on_begin_play", True)
        spawner.set_editor_property("vat_time_offset_spread", 3.0)
        spawner.set_editor_property("visual_variants", visuals)

    final_spawner_count = require_single_spawner(spawner)

    if not unreal.EditorLevelLibrary.save_current_level():
        raise RuntimeError("failed to save /Game/Maps/shanghai")

    payload = {
        "status": "PASS",
        "configured_at_utc": utc_now(),
        "map": "/Game/Maps/shanghai",
        "pie_stopped": True,
        "map_file": str(map_file),
        "map_read_only_cleared": map_read_only_cleared,
        "spawner": spawner.get_path_name(),
        "previous_spawner_count": previous_spawner_count,
        "final_spawner_count": final_spawner_count,
        "saved_gate": 30,
        "network_asset": NETWORK_ASSET_PATH,
        "manifest": str(manifest_path),
        "manifest_sha256": sha256_file(manifest_path),
        "visual_variant_ids": list(EXPECTED_VARIANTS),
        "parts_per_variant": len(EXPECTED_PARTS),
        "resolved_asset_count": len(asset_paths),
        "lod_budget_limits": {
            "high_actor_maximum": 24,
            "low_actor_maximum": 72,
            "gate300_minimum_vat_remainder": 204,
            "these_are_configuration_limits_not_runtime_counts": True,
            "runtime_counts_require_gate_verifier": True,
        },
    }
    latest, timestamped = write_report(payload)
    unreal.log_warning(
        "OPEN_MASS_CENTRAL_SETUP={} reports={},{}".format(
            json.dumps(payload, ensure_ascii=False, sort_keys=True),
            latest,
            timestamped,
        )
    )


try:
    main()
except Exception as error:
    unreal.log_error("OPEN_MASS_CENTRAL_SETUP_ERROR {}".format(error))
    raise
