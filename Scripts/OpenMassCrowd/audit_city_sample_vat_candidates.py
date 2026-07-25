"""Read-only preflight for the modular City Sample VAT manifest.

This proves every configured part is real visible geometry before the builder
creates or updates a single asset.  It also records the official City Sample
skeleton, selected LOD, material sections, VAT material-switch support and the
frame synchronization capacity of the part animation.
"""

from __future__ import annotations

import json
from pathlib import Path

import unreal


MANIFEST_RELATIVE_PATH = Path("Scripts/OpenMassCrowd/city_sample_vat_manifest.json")
SK_BASE = "/Game/CitySampleCrowd/Character/Shared/Rig/SK_Base.SK_Base"
FACE_SKELETON = (
    "/Game/CitySampleCrowd/Character/Shared/Rig/"
    "Face_Archetype_Skeleton.Face_Archetype_Skeleton"
)


def _path(asset: object | None) -> str:
    return asset.get_path_name() if asset is not None else ""


def _used_slots(subsystem: object, mesh: object, lod_index: int) -> list[int]:
    result = []
    for section in range(int(subsystem.get_num_sections(mesh, lod_index))):
        slot = int(subsystem.get_lod_material_slot(mesh, lod_index, section))
        result.append(section if slot < 0 else slot)
    return sorted(set(result))


def _has_bone_switch(material: object) -> bool:
    return "UseBoneAnimation" in {
        str(name)
        for name in unreal.MaterialEditingLibrary.get_static_switch_parameter_names(
            material
        )
    }


def main() -> None:
    manifest_path = Path(unreal.Paths.project_dir()) / MANIFEST_RELATIVE_PATH
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    subsystem = unreal.get_editor_subsystem(unreal.SkeletalMeshEditorSubsystem)
    rows = []
    failures = []
    for variant in manifest.get("variants", []):
        variant_id = str(variant.get("id", ""))
        walk_path = str(variant.get("animation", ""))
        walk = unreal.load_asset(walk_path)
        frame_count = (
            int(unreal.AnimationLibrary.get_num_keys(walk))
            if isinstance(walk, unreal.AnimSequence)
            else 0
        )
        for part in variant.get("parts", []):
            part_id = str(part.get("id", ""))
            role = str(part.get("role", ""))
            mesh_path = str(part.get("skeletal_mesh", ""))
            animation_path = str(part.get("animation", walk_path))
            lod_index = int(part.get("skeletal_lod", -1))
            mesh = unreal.load_asset(mesh_path)
            animation = unreal.load_asset(animation_path)
            part_failures = []
            row = {
                "variant": variant_id,
                "part": part_id,
                "role": role,
                "mesh": mesh_path,
                "animation": animation_path,
                "required_frame_count": frame_count,
            }
            if not isinstance(mesh, unreal.SkeletalMesh):
                part_failures.append("mesh missing/wrong class")
            else:
                skeleton_path = _path(mesh.get_editor_property("skeleton"))
                lod_count = int(subsystem.get_lod_count(mesh))
                row["skeleton"] = skeleton_path
                row["lod_count"] = lod_count
                if not 0 <= lod_index < lod_count:
                    part_failures.append("invalid selected LOD")
                else:
                    vertices = int(subsystem.get_num_verts(mesh, lod_index))
                    slots = _used_slots(subsystem, mesh, lod_index)
                    materials = list(mesh.get_editor_property("materials"))
                    material_rows = []
                    for slot in slots:
                        material = (
                            materials[slot].material_interface
                            if slot < len(materials)
                            else None
                        )
                        material_rows.append(
                            {
                                "slot": slot,
                                "path": _path(material),
                                "use_bone_animation": (
                                    _has_bone_switch(material)
                                    if material is not None
                                    else False
                                ),
                            }
                        )
                    bounds = mesh.get_bounds()
                    row.update(
                        {
                            "selected_lod": lod_index,
                            "vertices": vertices,
                            "sections": int(
                                subsystem.get_num_sections(mesh, lod_index)
                            ),
                            "used_material_slots": slots,
                            "materials": material_rows,
                            "bounds_z": [
                                float(bounds.origin.z - bounds.box_extent.z),
                                float(bounds.origin.z + bounds.box_extent.z),
                            ],
                        }
                    )
                    if vertices < 100:
                        part_failures.append("selected LOD is not visible geometry")
                expected_skeleton = FACE_SKELETON if role == "head" else SK_BASE
                if skeleton_path != expected_skeleton:
                    part_failures.append("role skeleton mismatch")
            if "_base." in mesh_path.lower():
                part_failures.append("forbidden *_base proxy")
            if not isinstance(animation, unreal.AnimSequence):
                part_failures.append("animation missing/wrong class")
            else:
                available = int(unreal.AnimationLibrary.get_num_keys(animation))
                row["available_frame_count"] = available
                if available < frame_count:
                    part_failures.append("animation cannot match walk frame count")
            if role == "head":
                fallback = unreal.load_asset(str(part.get("material_fallback", "")))
                row["material_fallback"] = _path(fallback)
                if fallback is None or not _has_bone_switch(fallback):
                    part_failures.append("head fallback lacks UseBoneAnimation")
            row["failures"] = part_failures
            rows.append(row)
            failures.extend(
                f"{variant_id}/{part_id}: {failure}" for failure in part_failures
            )

    report = {
        "schema_version": 2,
        "status": "PASS" if not failures and len(rows) == 24 else "FAIL",
        "read_only": True,
        "variant_count": len(manifest.get("variants", [])),
        "part_count": len(rows),
        "failures": failures,
        "parts": rows,
    }
    print("OPEN_MASS_CITY_SAMPLE_VAT_AUDIT=" + json.dumps(report, sort_keys=True))
    if report["status"] != "PASS":
        raise RuntimeError("City Sample VAT source audit failed")


if __name__ == "__main__":
    main()
