"""Audit City Sample Crowds assets in the dedicated staging project.

Run this script with the staging project's UnrealEditor-Cmd process, for
example::

    UnrealEditor-Cmd.exe ^
      D:/CitySampleCrowds_Staging/CSCStage.uproject ^
      -ExecutePythonScript=.../audit_city_sample_staging_assets.py ^
      -unattended -nop4 -nosplash -nullrhi

Unreal mounts the current project's ``Content`` directory at ``/Game``.  The
script therefore scans ``/Game`` and separately reports whether the current
project is actually below ``D:/CitySampleCrowds_Staging`` so that a report from
the wrong project cannot be mistaken for a successful staging audit.

The audit is read-only except for its optional JSON report.  Set the process
environment variable ``OPEN_MASS_WRITE_JSON=0`` to suppress that file.
"""

from __future__ import annotations

import json
import os
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import unreal


EXPECTED_STAGING_ROOT = Path("D:/CitySampleCrowds_Staging")
REPORT_PATH = EXPECTED_STAGING_ROOT / "Saved/Reports/city_sample_crowds_asset_audit.json"
REGISTRY_ROOT = "/Game"
WRITE_JSON_REPORT = os.environ.get("OPEN_MASS_WRITE_JSON", "1").strip().lower() not in {
    "0",
    "false",
    "no",
    "off",
}

VAT_MARKERS = ("animtotexture", "anim_to_texture", "vertexanimation", "vertex_animation", "vat")
DEPENDENCY_KEYWORDS = (
    "animtotexture",
    "citysample",
    "crowd",
    "hairstrands",
    "massai",
    "masscrowd",
    "massentity",
    "massmovement",
    "massrepresentation",
    "metahuman",
    "niagara",
    "stateTree",
    "zonegraph",
)
BLUEPRINT_TAGS = (
    "ParentClass",
    "NativeParentClass",
    "GeneratedClass",
    "ImplementedInterfaces",
    "Skeleton",
    "TargetSkeleton",
)
SCRIPT_MODULE_PATTERN = re.compile(r"/Script/([A-Za-z0-9_]+)", re.IGNORECASE)


def _asset_class_path(asset_data):
    class_path = asset_data.asset_class_path
    # In UE 5.7 ``str(TopLevelAssetPath)`` includes a transient memory address,
    # so it cannot be used for class comparisons or deterministic reports.
    try:
        return f"{class_path.package_name}.{class_path.asset_name}"
    except AttributeError:
        return str(class_path)


def _asset_class_name(asset_data):
    try:
        return str(asset_data.asset_class_path.asset_name)
    except AttributeError:
        return _asset_class_path(asset_data).rsplit(".", 1)[-1]


def _object_path(asset_data):
    # UE 5.7 removed ``AssetData.get_soft_object_path`` from the Python
    # binding.  Top-level Unreal assets always use ``Package.Asset`` as their
    # object path, and these registry fields are available across UE 5.x.
    return f"{asset_data.package_name}.{asset_data.asset_name}"


def _searchable_asset_text(asset_data):
    return " ".join(
        (
            str(asset_data.package_name),
            str(asset_data.package_path),
            str(asset_data.asset_name),
            _asset_class_path(asset_data),
        )
    ).lower()


def _tag_value(asset_data, tag_name):
    """Return an AssetData tag as text across supported UE Python variants."""

    try:
        value = asset_data.get_tag_value(tag_name)
    except Exception:
        return ""
    if value is None:
        return ""
    if isinstance(value, tuple):
        # Some engine versions expose TryGet-style results as (success, value).
        if len(value) == 2 and isinstance(value[0], bool):
            return str(value[1]) if value[0] else ""
        return " ".join(str(item) for item in value)
    return str(value)


def _is_blueprint(asset_data):
    return "blueprint" in _asset_class_name(asset_data).lower()


def _is_bp_crowd_character(asset_data):
    if not _is_blueprint(asset_data):
        return False
    return str(asset_data.asset_name).lower() == "bp_crowdcharacter"


def _is_crowd_data_asset(asset_data):
    class_name = _asset_class_name(asset_data).lower()
    return "dataasset" in class_name and "crowd" in _searchable_asset_text(asset_data)


def _is_anim_blueprint(asset_data):
    return "animblueprint" in _asset_class_name(asset_data).lower()


def _is_skeletal_mesh(asset_data):
    return _asset_class_name(asset_data).lower() == "skeletalmesh"


def _is_vat_or_anim_to_texture(asset_data):
    searchable = _searchable_asset_text(asset_data)
    if any(marker in searchable for marker in VAT_MARKERS):
        return True
    return any(
        any(marker in _tag_value(asset_data, tag_name).lower() for marker in VAT_MARKERS)
        for tag_name in BLUEPRINT_TAGS
    )


def _paths(assets):
    return sorted({_object_path(asset) for asset in assets})


def _category(assets):
    paths = _paths(assets)
    return {"count": len(paths), "paths": paths}


def _project_is_under_staging_root(project_dir):
    try:
        project_path = Path(project_dir).resolve()
        staging_path = EXPECTED_STAGING_ROOT.resolve()
        return project_path == staging_path or staging_path in project_path.parents
    except (OSError, RuntimeError):
        project_path = os.path.normcase(os.path.abspath(project_dir))
        staging_path = os.path.normcase(os.path.abspath(str(EXPECTED_STAGING_ROOT)))
        return os.path.commonpath((project_path, staging_path)) == staging_path


def _get_dependencies(registry, package_name):
    """Query package dependencies while tolerating UE minor-version bindings."""

    try:
        return registry.get_dependencies(package_name)
    except TypeError:
        return registry.get_dependencies(package_name, unreal.AssetRegistryDependencyOptions())


def _dependency_clues(registry, all_assets, key_assets):
    script_modules = Counter()
    plugin_roots = Counter()
    noteworthy_packages = set()
    failures = []

    def inspect_reference(reference):
        reference_text = str(reference)
        for module in SCRIPT_MODULE_PATTERN.findall(reference_text):
            script_modules[module] += 1

        if reference_text.startswith("/"):
            root = reference_text.split("/", 2)[1]
            if root and root.lower() not in {"game", "engine", "script"}:
                plugin_roots[root] += 1

        lowered = reference_text.lower().replace("_", "")
        if any(keyword.lower().replace("_", "") in lowered for keyword in DEPENDENCY_KEYWORDS):
            noteworthy_packages.add(reference_text)

    # Every AssetData class path reveals at least the native module that owns
    # the asset type; Blueprint tags often reveal the native parent module.
    for asset in all_assets:
        inspect_reference(_asset_class_path(asset))
        for tag_name in BLUEPRINT_TAGS:
            tag_value = _tag_value(asset, tag_name)
            if tag_value:
                inspect_reference(tag_value)

    # Dependency walks are limited to assets relevant to crowd integration so
    # the commandlet remains fast even if the staging project grows later.
    for asset in key_assets:
        package_name = str(asset.package_name)
        try:
            for dependency in _get_dependencies(registry, asset.package_name):
                inspect_reference(dependency)
        except Exception as error:
            failures.append({"package": package_name, "error": repr(error)})

    return {
        "key_asset_dependency_queries": len(key_assets),
        "script_modules": dict(sorted(script_modules.items())),
        "plugin_content_roots": dict(sorted(plugin_roots.items())),
        "noteworthy_packages": sorted(noteworthy_packages),
        "query_failures": failures,
    }


def _emit(marker, value):
    print(marker + "=" + json.dumps(value, ensure_ascii=False, sort_keys=True))


def main():
    registry = unreal.AssetRegistryHelpers.get_asset_registry()
    registry.scan_paths_synchronous([REGISTRY_ROOT], force_rescan=True)
    all_assets = list(registry.get_assets_by_path(REGISTRY_ROOT, recursive=True))

    bp_crowd_characters = [asset for asset in all_assets if _is_bp_crowd_character(asset)]
    crowd_data_assets = [asset for asset in all_assets if _is_crowd_data_asset(asset)]
    anim_blueprints = [asset for asset in all_assets if _is_anim_blueprint(asset)]
    skeletal_meshes = [asset for asset in all_assets if _is_skeletal_mesh(asset)]
    vat_assets = [asset for asset in all_assets if _is_vat_or_anim_to_texture(asset)]

    key_assets_by_path = {
        _object_path(asset): asset
        for asset in (
            bp_crowd_characters
            + crowd_data_assets
            + anim_blueprints
            + skeletal_meshes
            + vat_assets
        )
    }
    key_assets = [key_assets_by_path[path] for path in sorted(key_assets_by_path)]

    project_dir = unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_dir())
    categories = {
        "bp_crowd_character": _category(bp_crowd_characters),
        "crowd_data_assets": _category(crowd_data_assets),
        "animation_blueprints": _category(anim_blueprints),
        "skeletal_meshes": _category(skeletal_meshes),
        "vat_anim_to_texture": _category(vat_assets),
    }
    dependency_clues = _dependency_clues(registry, all_assets, key_assets)
    class_counts = Counter(_asset_class_name(asset) for asset in all_assets)

    report = {
        "schema_version": 1,
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "expected_staging_root": str(EXPECTED_STAGING_ROOT),
        "current_project_dir": project_dir,
        "staging_project_match": _project_is_under_staging_root(project_dir),
        "registry_root": REGISTRY_ROOT,
        "game_asset_count": len(all_assets),
        "class_counts": dict(sorted(class_counts.items())),
        "categories": categories,
        "dependency_clues": dependency_clues,
        "readiness": {
            "has_bp_crowd_character": bool(bp_crowd_characters),
            "has_crowd_data_asset": bool(crowd_data_assets),
            "has_animation_blueprint": bool(anim_blueprints),
            "has_skeletal_mesh": bool(skeletal_meshes),
            "has_vat_or_anim_to_texture": bool(vat_assets),
        },
    }

    _emit(
        "OPEN_MASS_CITY_SAMPLE_STAGING_CONTEXT",
        {
            "current_project_dir": project_dir,
            "expected_staging_root": str(EXPECTED_STAGING_ROOT),
            "staging_project_match": report["staging_project_match"],
            "registry_root": REGISTRY_ROOT,
            "game_asset_count": len(all_assets),
        },
    )
    _emit("OPEN_MASS_BP_CROWD_CHARACTER_ASSETS", categories["bp_crowd_character"])
    _emit("OPEN_MASS_CROWD_DATA_ASSETS", categories["crowd_data_assets"])
    _emit("OPEN_MASS_ANIMATION_BLUEPRINT_ASSETS", categories["animation_blueprints"])
    _emit("OPEN_MASS_SKELETAL_MESH_ASSETS", categories["skeletal_meshes"])
    _emit("OPEN_MASS_VAT_ANIM_TO_TEXTURE_ASSETS", categories["vat_anim_to_texture"])
    _emit("OPEN_MASS_DEPENDENCY_MODULE_CLUES", dependency_clues)

    if WRITE_JSON_REPORT:
        try:
            REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
            REPORT_PATH.write_text(
                json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            _emit("OPEN_MASS_CITY_SAMPLE_STAGING_REPORT", {"written": True, "path": str(REPORT_PATH)})
        except Exception as error:
            _emit(
                "OPEN_MASS_CITY_SAMPLE_STAGING_REPORT",
                {"written": False, "path": str(REPORT_PATH), "error": repr(error)},
            )
    else:
        _emit(
            "OPEN_MASS_CITY_SAMPLE_STAGING_REPORT",
            {"written": False, "disabled": True, "path": str(REPORT_PATH)},
        )

    _emit("OPEN_MASS_CITY_SAMPLE_STAGING_AUDIT", report)


main()
