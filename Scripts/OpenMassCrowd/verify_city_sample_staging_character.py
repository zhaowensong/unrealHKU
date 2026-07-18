"""Load, compile, instantiate, and size the official City Sample crowd actor.

This runs only in the D-drive staging project.  It is deliberately read-only
with respect to Unreal assets; the only output is a JSON report under Saved.
"""

from __future__ import annotations

import json
from collections import Counter, deque
from datetime import datetime, timezone
from pathlib import Path

import unreal


STAGING_ROOT = Path("D:/CitySampleCrowds_Staging")
REPORT_PATH = STAGING_ROOT / "Saved/Reports/city_sample_character_ue57_verification.json"
CHARACTER_BP_PATH = "/Game/CitySampleCrowd/Blueprints/BP_CrowdCharacter"
CHARACTER_DATA_PATH = "/Game/CitySampleCrowd/Character/Shared/Data/CrowdCharacterDataAsset"
SHOWCASE_MAP_PATH = "/Game/CitySampleCrowd/Maps/CitySampleCrowd_LVL"


def emit(marker, value):
    print(marker + "=" + json.dumps(value, ensure_ascii=False, sort_keys=True))


def object_path(value):
    if value is None:
        return None
    try:
        return value.get_path_name()
    except Exception:
        return str(value)


def class_name(value):
    if value is None:
        return None
    try:
        return value.get_class().get_name()
    except Exception:
        return type(value).__name__


def compile_blueprint(blueprint):
    result = {"attempted": False, "succeeded": False, "error": None}
    try:
        result["attempted"] = True
        unreal.BlueprintEditorLibrary.compile_blueprint(blueprint)
        result["succeeded"] = True
    except Exception as error:
        result["error"] = repr(error)
    return result


def generated_class_for(blueprint):
    try:
        return blueprint.generated_class()
    except Exception:
        try:
            return blueprint.get_editor_property("generated_class")
        except Exception:
            return None


def relevant_python_names(value):
    markers = (
        "anim",
        "body",
        "character",
        "crowd",
        "data",
        "gender",
        "hair",
        "outfit",
        "random",
        "seed",
        "weight",
    )
    return sorted(
        name
        for name in dir(value)
        if not name.startswith("_") and any(marker in name.lower() for marker in markers)
    )


def skeletal_component_summary(actor):
    summaries = []
    for component in actor.get_components_by_class(unreal.SkeletalMeshComponent):
        mesh = None
        for property_name in ("skeletal_mesh_asset", "skeletal_mesh"):
            try:
                mesh = component.get_editor_property(property_name)
                break
            except Exception:
                pass

        anim_class = None
        try:
            anim_class = component.get_editor_property("anim_class")
        except Exception:
            pass

        summaries.append(
            {
                "component": component.get_name(),
                "class": component.get_class().get_name(),
                "visible": bool(component.is_visible()),
                "skeletal_mesh": object_path(mesh),
                "anim_class": object_path(anim_class),
            }
        )
    return summaries


def dependency_options(include_soft):
    options = unreal.AssetRegistryDependencyOptions()
    requested = {
        "include_hard_package_references": True,
        "include_soft_package_references": include_soft,
        "include_hard_management_references": True,
        "include_soft_management_references": include_soft,
        "include_searchable_names": False,
    }
    applied = {}
    for name, value in requested.items():
        try:
            options.set_editor_property(name, value)
            applied[name] = value
        except Exception:
            applied[name] = "unsupported"
    return options, applied


def dependency_closure(registry, seeds, include_soft):
    options, applied = dependency_options(include_soft)
    queue = deque(seeds)
    visited = set()
    game_packages = set()
    engine_packages = set()
    script_packages = set()
    external_packages = set()
    failures = []

    while queue:
        package = str(queue.popleft())
        if package in visited:
            continue
        visited.add(package)

        if package.startswith("/Game/"):
            game_packages.add(package)
        elif package.startswith("/Engine/"):
            engine_packages.add(package)
        elif package.startswith("/Script/"):
            script_packages.add(package)
        elif package.startswith("/"):
            external_packages.add(package)

        try:
            dependencies = registry.get_dependencies(package, options)
        except Exception as error:
            failures.append({"package": package, "error": repr(error)})
            continue

        for dependency in dependencies:
            dependency_text = str(dependency)
            # Only project assets need recursive traversal for migration.
            # Walking Engine/Script dependencies expands into most of UE and
            # is both irrelevant to copying Content and prohibitively slow.
            if dependency_text.startswith("/Game/") and dependency_text not in visited:
                queue.append(dependency_text)
            elif dependency_text.startswith("/Engine/"):
                engine_packages.add(dependency_text)
            elif dependency_text.startswith("/Script/"):
                script_packages.add(dependency_text)
            elif dependency_text.startswith("/"):
                external_packages.add(dependency_text)

    return {
        "options": applied,
        "visited_count": len(visited),
        "game_packages": sorted(game_packages),
        "engine_package_count": len(engine_packages),
        "script_packages": sorted(script_packages),
        "external_packages": sorted(external_packages),
        "query_failures": failures,
    }


def game_package_files(packages):
    files = []
    missing = []
    total_bytes = 0
    content_root = STAGING_ROOT / "Content"

    for package in packages:
        relative = package.removeprefix("/Game/")
        stem = content_root / Path(relative)
        package_files = []
        for suffix in (".uasset", ".umap", ".uexp", ".ubulk", ".uptnl"):
            candidate = Path(str(stem) + suffix)
            if candidate.is_file():
                size = candidate.stat().st_size
                total_bytes += size
                package_files.append({"path": str(candidate), "bytes": size})
        if package_files:
            files.extend(package_files)
        else:
            missing.append(package)

    return {
        "file_count": len(files),
        "total_bytes": total_bytes,
        "total_gib": round(total_bytes / (1024**3), 3),
        "missing_packages": missing,
        "files": files,
    }


def main():
    project_dir = Path(
        unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_dir())
    ).resolve()
    staging_match = project_dir == STAGING_ROOT.resolve()
    if not staging_match:
        raise RuntimeError(
            "Refusing to verify outside the City Sample staging project: {}".format(
                project_dir
            )
        )

    character_bp = unreal.load_asset(CHARACTER_BP_PATH)
    character_data = unreal.load_asset(CHARACTER_DATA_PATH)
    compile_result = compile_blueprint(character_bp) if character_bp else {
        "attempted": False,
        "succeeded": False,
        "error": "Blueprint failed to load",
    }
    generated_class = generated_class_for(character_bp) if character_bp else None
    default_object = unreal.get_default_object(generated_class) if generated_class else None
    actor_subclass = isinstance(default_object, unreal.Actor)

    world = unreal.EditorLoadingAndSavingUtils.load_map(SHOWCASE_MAP_PATH)
    actor_subsystem = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    actors = actor_subsystem.get_all_level_actors() if world else []
    actor_class_counts = Counter(actor.get_class().get_name() for actor in actors)
    crowd_actors = [
        actor
        for actor in actors
        if "BP_CrowdCharacter" in actor.get_class().get_name()
    ]

    instances = []
    visible_mesh_instances = 0
    for actor in crowd_actors:
        components = skeletal_component_summary(actor)
        if any(item["visible"] and item["skeletal_mesh"] for item in components):
            visible_mesh_instances += 1
        if len(instances) < 12:
            location = actor.get_actor_location()
            instances.append(
                {
                    "label": actor.get_actor_label(),
                    "class": actor.get_class().get_name(),
                    "location": [location.x, location.y, location.z],
                    "skeletal_components": components,
                }
            )

    registry = unreal.AssetRegistryHelpers.get_asset_registry()
    seeds = [CHARACTER_BP_PATH, CHARACTER_DATA_PATH]
    hard_closure = dependency_closure(registry, seeds, include_soft=False)
    all_closure = dependency_closure(registry, seeds, include_soft=True)
    hard_files = game_package_files(hard_closure["game_packages"])
    all_files = game_package_files(all_closure["game_packages"])

    failure_reasons = []
    checks = {
        "staging_project_match": staging_match,
        "blueprint_loaded": character_bp is not None,
        "blueprint_compiled": compile_result["succeeded"],
        "generated_class_present": generated_class is not None,
        "generated_class_is_actor": actor_subclass,
        "data_asset_loaded": character_data is not None,
        "showcase_map_loaded": world is not None,
        "showcase_has_crowd_instances": bool(crowd_actors),
        "showcase_has_visible_skeletal_meshes": visible_mesh_instances > 0,
        "hard_dependency_query_clean": not hard_closure["query_failures"],
        "all_dependency_query_clean": not all_closure["query_failures"],
    }
    failure_reasons.extend(name for name, passed in checks.items() if not passed)

    report = {
        "schema_version": 1,
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "engine_version": unreal.SystemLibrary.get_engine_version(),
        "project_dir": str(project_dir),
        "audit_pass": not failure_reasons,
        "failure_reasons": failure_reasons,
        "checks": checks,
        "blueprint": {
            "path": CHARACTER_BP_PATH,
            "loaded_class": class_name(character_bp),
            "compile": compile_result,
            "generated_class": object_path(generated_class),
            "default_object_class": class_name(default_object),
            "actor_subclass": actor_subclass,
            "relevant_python_names": relevant_python_names(default_object)
            if default_object
            else [],
        },
        "data_asset": {
            "path": CHARACTER_DATA_PATH,
            "loaded_class": class_name(character_data),
            "relevant_python_names": relevant_python_names(character_data)
            if character_data
            else [],
        },
        "showcase": {
            "map": SHOWCASE_MAP_PATH,
            "world": object_path(world),
            "actor_count": len(actors),
            "actor_class_counts": dict(sorted(actor_class_counts.items())),
            "crowd_actor_count": len(crowd_actors),
            "visible_skeletal_mesh_instance_count": visible_mesh_instances,
            "sample_instances": instances,
        },
        "dependencies": {
            "seeds": seeds,
            "hard": hard_closure,
            "hard_files": hard_files,
            "hard_and_soft": all_closure,
            "hard_and_soft_files": all_files,
        },
    }

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    emit(
        "OPEN_MASS_CITY_SAMPLE_CHARACTER_VERIFY",
        {
            "audit_pass": report["audit_pass"],
            "failure_reasons": failure_reasons,
            "engine_version": report["engine_version"],
            "generated_class": report["blueprint"]["generated_class"],
            "crowd_actor_count": len(crowd_actors),
            "visible_skeletal_mesh_instance_count": visible_mesh_instances,
            "hard_game_packages": len(hard_closure["game_packages"]),
            "hard_gib": hard_files["total_gib"],
            "all_game_packages": len(all_closure["game_packages"]),
            "all_gib": all_files["total_gib"],
            "report": str(REPORT_PATH),
        },
    )
    if failure_reasons:
        raise RuntimeError("City Sample character verification failed: " + ", ".join(failure_reasons))


main()
