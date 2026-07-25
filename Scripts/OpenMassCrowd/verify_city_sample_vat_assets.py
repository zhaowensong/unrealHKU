"""Strict read-only verifier for six City Sample modular VAT assemblies.

The verifier rejects the three-vertex ``*_base`` animation drivers, checks all
24 visible source/output parts, confirms body texture sharing and synchronized
autoplay frame ranges, and validates that each assembly covers a complete human
silhouette from shoes through an official City Sample face.  It never edits or
saves Unreal assets.  Material parameter inspection remains GLOBAL_PARAMETER
only because UE 5.7 asserts on invalid Layer/Blend indices passed from Python.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import unreal


MANIFEST_RELATIVE_PATH = Path("Scripts/OpenMassCrowd/city_sample_vat_manifest.json")
EXPECTED_OUTPUT_ROOT = "/Game/OpenMassCrowd/CitySampleVAT"
EXPECTED_VARIANT_IDS = ("FTN", "FTO", "FTU", "MTN", "MTO", "MTU")
EXPECTED_PART_ROLES = {"torso", "legs", "shoes", "head"}
CITY_SAMPLE_ROOT = "/Game/CitySampleCrowd/"
SK_BASE = "/Game/CitySampleCrowd/Character/Shared/Rig/SK_Base.SK_Base"
FACE_SKELETON = (
    "/Game/CitySampleCrowd/Character/Shared/Rig/"
    "Face_Archetype_Skeleton.Face_Archetype_Skeleton"
)
GLOBAL_ASSOCIATION = unreal.MaterialParameterAssociation.GLOBAL_PARAMETER


def _path(asset: object | None) -> str:
    return asset.get_path_name() if asset is not None else ""


def _object_path(package_path: str) -> str:
    return f"{package_path}.{package_path.rsplit('/', 1)[-1]}"


def _part_paths(root: str, variant_id: str, part_id: str, group: str) -> dict:
    variant_root = f"{root}/{variant_id}"
    prefix = f"CitySample_{variant_id}_{part_id}"
    return {
        "static_mesh": f"{variant_root}/SM_{prefix}_VAT",
        "animation_data": f"{variant_root}/DA_{prefix}_VAT",
        "bone_position_texture": (
            f"{variant_root}/TX_{variant_id}_{group}_BonePosition"
        ),
        "bone_rotation_texture": (
            f"{variant_root}/TX_{variant_id}_{group}_BoneRotation"
        ),
        "bone_weight_texture": f"{variant_root}/TX_{variant_id}_{part_id}_BoneWeight",
        "material_prefix": f"{variant_root}/MI_{prefix}_S",
    }


def _record_check(
    checks: list[dict[str, object]], name: str, passed: bool, detail: object
) -> None:
    checks.append({"name": name, "passed": bool(passed), "detail": detail})


def _asset_dependencies(package_name: str) -> list[str]:
    registry = unreal.AssetRegistryHelpers.get_asset_registry()
    options = unreal.AssetRegistryDependencyOptions(
        include_soft_package_references=True,
        include_hard_package_references=True,
        include_searchable_names=False,
        include_soft_management_references=True,
        include_hard_management_references=True,
    )
    return sorted(str(item) for item in registry.get_dependencies(package_name, options))


def _vector_record(vector: object) -> dict[str, float]:
    return {"x": float(vector.x), "y": float(vector.y), "z": float(vector.z)}


def _material_has_bone_switch(material: object) -> bool:
    return "UseBoneAnimation" in {
        str(name)
        for name in unreal.MaterialEditingLibrary.get_static_switch_parameter_names(
            material
        )
    }


def _skeletal_used_slots(
    subsystem: object, skeletal_mesh: object, lod_index: int
) -> list[int]:
    section_count = int(subsystem.get_num_sections(skeletal_mesh, lod_index))
    slots = []
    for section in range(section_count):
        slot = int(subsystem.get_lod_material_slot(skeletal_mesh, lod_index, section))
        # The base LOD commonly reports INDEX_NONE when section and slot match.
        slots.append(section if slot < 0 else slot)
    return sorted(set(slots))


def _static_used_slots(
    subsystem: object, static_mesh: object, lod_index: int
) -> list[int]:
    section_count = int(static_mesh.get_num_sections(lod_index))
    slots = []
    for section in range(section_count):
        slot = int(subsystem.get_lod_material_slot(static_mesh, lod_index, section))
        slots.append(section if slot < 0 else slot)
    return sorted(set(slots))


def _expected_precision(name: str) -> object:
    if name == "eight_bits":
        return unreal.AnimToTexturePrecision.EIGHT_BITS
    if name == "sixteen_bits":
        return unreal.AnimToTexturePrecision.SIXTEEN_BITS
    return None


def _raw_mesh_bounds(
    static_mesh: object, lod_index: int
) -> tuple[tuple[float, float, float, float, float, float], int]:
    """Measure source vertices, excluding AnimToTexture's culling extension."""
    description = static_mesh.get_static_mesh_description(lod_index)
    vertex_count = int(description.get_vertex_count())
    positions = [
        description.get_vertex_position(unreal.VertexID(index))
        for index in range(vertex_count)
    ]
    if not positions:
        return (0.0, 0.0, 0.0, 0.0, 0.0, 0.0), 0
    return (
        min(float(item.x) for item in positions),
        max(float(item.x) for item in positions),
        min(float(item.y) for item in positions),
        max(float(item.y) for item in positions),
        min(float(item.z) for item in positions),
        max(float(item.z) for item in positions),
    ), vertex_count


def _is_base_proxy_dependency(path: str) -> bool:
    lower = path.lower()
    basename = lower.rsplit("/", 1)[-1]
    return "/meshes/" in lower and basename.endswith("_base")


def main() -> None:
    manifest_path = Path(unreal.Paths.project_dir()) / MANIFEST_RELATIVE_PATH
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes.decode("utf-8"))
    checks: list[dict[str, object]] = []
    records: list[dict[str, object]] = []
    expected_assets: set[str] = set()

    output_root = str(manifest.get("output_root", "")).rstrip("/")
    variants = manifest.get("variants")
    variant_ids = (
        tuple(str(item.get("id", "")) for item in variants)
        if isinstance(variants, list)
        else ()
    )
    _record_check(
        checks, "manifest_schema", manifest.get("schema_version") == 2,
        manifest.get("schema_version")
    )
    _record_check(checks, "output_root", output_root == EXPECTED_OUTPUT_ROOT, output_root)
    _record_check(
        checks,
        "six_official_assemblies",
        set(variant_ids) == set(EXPECTED_VARIANT_IDS) and len(variant_ids) == 6,
        variant_ids,
    )
    if not isinstance(variants, list):
        variants = []

    static_subsystem = unreal.get_editor_subsystem(unreal.StaticMeshEditorSubsystem)
    skeletal_subsystem = unreal.get_editor_subsystem(
        unreal.SkeletalMeshEditorSubsystem
    )
    configured_part_count = 0
    for variant in variants:
        variant_id = str(variant.get("id", ""))
        variant_animation_path = str(variant.get("animation", ""))
        variant_animation = unreal.load_asset(variant_animation_path)
        target_frame_count = (
            int(unreal.AnimationLibrary.get_num_keys(variant_animation))
            if isinstance(variant_animation, unreal.AnimSequence)
            else 0
        )
        parts = variant.get("parts")
        if not isinstance(parts, list):
            parts = []
        configured_part_count += len(parts)
        assembly_failures: list[str] = []
        roles = {str(part.get("role", "")) for part in parts}
        if len(parts) != 4 or roles != EXPECTED_PART_ROLES:
            assembly_failures.append("assembly does not contain torso/legs/shoes/head")
        if not isinstance(variant_animation, unreal.AnimSequence):
            assembly_failures.append("walk animation missing/wrong class")
        if (
            not variant_animation_path.startswith(CITY_SAMPLE_ROOT)
            or "_Walk_F" not in variant_animation_path
        ):
            assembly_failures.append("variant animation is not an official walk")
        if target_frame_count < 2:
            assembly_failures.append("walk animation has no usable frame range")

        part_records = []
        # AnimToTexture expands UObject bounds to the whole animated skeleton to
        # prevent culling.  Assembly coverage is therefore measured directly
        # from each generated StaticMesh's raw vertex positions.
        raw_bounds_by_role: dict[
            str, tuple[float, float, float, float, float, float]
        ] = {}
        body_position_textures: set[str] = set()
        body_rotation_textures: set[str] = set()
        body_frame_ranges: set[tuple[int, int, int]] = set()
        body_bone_counts: set[int] = set()
        total_vertices = 0
        for part in parts:
            part_id = str(part.get("id", ""))
            role = str(part.get("role", ""))
            group = str(part.get("texture_group", ""))
            paths = _part_paths(output_root, variant_id, part_id, group)
            part_failures: list[str] = []
            source_mesh_path = str(part.get("skeletal_mesh", ""))
            source_animation_path = str(
                part.get("animation", variant_animation_path)
            )
            declared_skeleton = str(part.get("skeleton", ""))
            skeletal_lod = int(part.get("skeletal_lod", -1))
            source_mesh = unreal.load_asset(source_mesh_path)
            source_animation = unreal.load_asset(source_animation_path)
            fallback_path = str(part.get("material_fallback", ""))
            fallback_material = unreal.load_asset(fallback_path) if fallback_path else None

            if not isinstance(source_mesh, unreal.SkeletalMesh):
                part_failures.append("source skeletal mesh missing/wrong class")
            if not isinstance(source_animation, unreal.AnimSequence):
                part_failures.append("source animation missing/wrong class")
            if not source_mesh_path.startswith(CITY_SAMPLE_ROOT):
                part_failures.append("non-City-Sample source mesh")
            if "_base." in source_mesh_path.lower():
                part_failures.append("forbidden three-vertex *_base proxy selected")
            if not source_animation_path.startswith(CITY_SAMPLE_ROOT):
                part_failures.append("non-City-Sample animation")

            source_used_slots: list[int] = []
            source_vertex_count = 0
            source_bounds_record: dict[str, object] = {}
            source_materials = []
            if isinstance(source_mesh, unreal.SkeletalMesh):
                skeleton = source_mesh.get_editor_property("skeleton")
                actual_skeleton = _path(skeleton)
                if actual_skeleton != declared_skeleton:
                    part_failures.append("source skeleton does not match manifest")
                expected_skeleton = FACE_SKELETON if role == "head" else SK_BASE
                if actual_skeleton != expected_skeleton:
                    part_failures.append("source skeleton is wrong for assembly role")
                lod_count = int(skeletal_subsystem.get_lod_count(source_mesh))
                if not 0 <= skeletal_lod < lod_count:
                    part_failures.append("source LOD index is invalid")
                else:
                    source_vertex_count = int(
                        skeletal_subsystem.get_num_verts(source_mesh, skeletal_lod)
                    )
                    source_used_slots = _skeletal_used_slots(
                        skeletal_subsystem, source_mesh, skeletal_lod
                    )
                    if source_vertex_count < 100:
                        part_failures.append(
                            "source LOD is empty or implausibly small "
                            f"(vertices={source_vertex_count})"
                        )
                source_materials = list(source_mesh.get_editor_property("materials"))
                source_bounds = source_mesh.get_bounds()
                source_origin = source_bounds.origin
                source_extent = source_bounds.box_extent
                source_bounds_record = {
                    "origin": _vector_record(source_origin),
                    "extent": _vector_record(source_extent),
                }

            # ConvertMeshesToStaticMesh collapses the selected skeletal LOD's
            # section materials to consecutive StaticMesh slots (notably face
            # LOD5 [14,1,3,4] becomes [0,1,2,3]).  Expected VAT material names
            # are therefore derived from the generated render sections below.
            expected_material_paths: dict[int, str] = {}

            generated = {
                "static_mesh": unreal.load_asset(_object_path(paths["static_mesh"])),
                "animation_data": unreal.load_asset(
                    _object_path(paths["animation_data"])
                ),
                "bone_position_texture": unreal.load_asset(
                    _object_path(paths["bone_position_texture"])
                ),
                "bone_rotation_texture": unreal.load_asset(
                    _object_path(paths["bone_rotation_texture"])
                ),
                "bone_weight_texture": unreal.load_asset(
                    _object_path(paths["bone_weight_texture"])
                ),
            }
            expected_assets.update(
                {
                    _object_path(paths["static_mesh"]),
                    _object_path(paths["animation_data"]),
                    _object_path(paths["bone_position_texture"]),
                    _object_path(paths["bone_rotation_texture"]),
                    _object_path(paths["bone_weight_texture"]),
                }
            )
            expected_classes = {
                "static_mesh": unreal.StaticMesh,
                "animation_data": unreal.AnimToTextureDataAsset,
                "bone_position_texture": unreal.Texture2D,
                "bone_rotation_texture": unreal.Texture2D,
                "bone_weight_texture": unreal.Texture2D,
            }
            for key, expected_class in expected_classes.items():
                if not isinstance(generated[key], expected_class):
                    part_failures.append(f"{key} missing/wrong class")

            data_asset = generated["animation_data"]
            data_detail: dict[str, object] = {}
            if isinstance(data_asset, unreal.AnimToTextureDataAsset):
                sequence_infos = list(data_asset.get_editor_property("anim_sequences"))
                sequence_paths = [
                    _path(info.get_editor_property("anim_sequence"))
                    for info in sequence_infos
                ]
                baked_ranges = list(data_asset.get_editor_property("animations"))
                range_pairs = [
                    (
                        int(item.get_editor_property("start_frame")),
                        int(item.get_editor_property("end_frame")),
                    )
                    for item in baked_ranges
                ]
                mode = data_asset.get_editor_property("mode")
                precision = data_asset.get_editor_property("precision")
                influences = data_asset.get_editor_property("num_bone_influences")
                num_frames = int(data_asset.get_editor_property("num_frames"))
                num_bones = int(data_asset.get_editor_property("num_bones"))
                animation_index = (
                    data_asset.get_index_from_anim_sequence(source_animation)
                    if isinstance(source_animation, unreal.AnimSequence)
                    else -1
                )
                data_detail = {
                    "mode": str(mode),
                    "precision": str(precision),
                    "num_bone_influences": str(influences),
                    "num_frames": num_frames,
                    "num_bones": num_bones,
                    "sample_rate": float(data_asset.get_editor_property("sample_rate")),
                    "uv_channel": int(data_asset.get_editor_property("uv_channel")),
                    "animation_lookup_index": int(animation_index),
                    "animation_sequences": sequence_paths,
                    "baked_ranges": range_pairs,
                }
                if mode != unreal.AnimToTextureMode.BONE:
                    part_failures.append("data asset is not Bone mode")
                if precision != _expected_precision(str(part.get("precision", ""))):
                    part_failures.append("data asset precision mismatch")
                if influences != unreal.AnimToTextureNumBoneInfluences.FOUR:
                    part_failures.append("data asset does not use four influences")
                if num_frames != target_frame_count or range_pairs != [
                    (0, target_frame_count - 1)
                ]:
                    part_failures.append("part autoplay frame range is not synchronized")
                if animation_index != 0 or sequence_paths != [source_animation_path]:
                    part_failures.append("animation lookup does not resolve part sequence")
                if _path(data_asset.get_editor_property("skeletal_mesh")) != source_mesh_path:
                    part_failures.append("data asset skeletal mesh mismatch")
                if _path(data_asset.get_editor_property("static_mesh")) != _object_path(
                    paths["static_mesh"]
                ):
                    part_failures.append("data asset static mesh mismatch")
                if int(data_asset.get_editor_property("skeletal_lod_index")) != skeletal_lod:
                    part_failures.append("data asset source LOD mismatch")
                if int(data_asset.get_editor_property("uv_channel")) != int(
                    manifest.get("uv_channel", -1)
                ):
                    part_failures.append("data asset UV channel mismatch")
                if abs(
                    float(data_asset.get_editor_property("sample_rate"))
                    - float(manifest.get("sample_rate", -1.0))
                ) > 0.001:
                    part_failures.append("data asset sample rate mismatch")
                if (
                    data_asset.get_editor_property("vertex_position_texture") is not None
                    or data_asset.get_editor_property("vertex_normal_texture") is not None
                ):
                    part_failures.append("unused Mannequin Vertex pointer remains")
                for key in (
                    "bone_position_texture",
                    "bone_rotation_texture",
                    "bone_weight_texture",
                ):
                    if _path(data_asset.get_editor_property(key)) != _object_path(paths[key]):
                        part_failures.append(f"data asset {key} mismatch")
                if role != "head":
                    body_position_textures.add(
                        _path(data_asset.get_editor_property("bone_position_texture"))
                    )
                    body_rotation_textures.add(
                        _path(data_asset.get_editor_property("bone_rotation_texture"))
                    )
                    body_frame_ranges.add(
                        (0, target_frame_count - 1, num_frames)
                    )
                    body_bone_counts.add(num_bones)
                if role == "head" and num_bones <= 256:
                    part_failures.append("face VAT unexpectedly omits high-index face bones")

            static_mesh = generated["static_mesh"]
            geometry_detail: dict[str, object] = {}
            if isinstance(static_mesh, unreal.StaticMesh):
                lod_count = int(static_mesh.get_num_lods())
                vertex_count = int(static_mesh.get_num_vertices(0)) if lod_count else 0
                triangle_count = int(static_mesh.get_num_triangles(0)) if lod_count else 0
                section_count = int(static_mesh.get_num_sections(0)) if lod_count else 0
                uv_count = (
                    int(static_subsystem.get_num_uv_channels(static_mesh, 0))
                    if lod_count
                    else 0
                )
                used_slots = (
                    _static_used_slots(static_subsystem, static_mesh, 0)
                    if lod_count
                    else []
                )
                expected_material_paths = {
                    slot: _object_path(
                        f"{paths['material_prefix']}{slot}_VAT"
                    )
                    for slot in used_slots
                }
                expected_assets.update(expected_material_paths.values())
                static_materials = list(
                    static_mesh.get_editor_property("static_materials")
                )
                material_paths = [
                    _path(slot.material_interface) for slot in static_materials
                ]
                bounds = static_mesh.get_bounds()
                origin = bounds.origin
                extent = bounds.box_extent
                raw_bounds, raw_vertex_count = _raw_mesh_bounds(static_mesh, 0)
                raw_bounds_by_role[role] = raw_bounds
                total_vertices += vertex_count
                geometry_detail = {
                    "lod_count": lod_count,
                    "lod0_vertices": vertex_count,
                    "lod0_triangles": triangle_count,
                    "lod0_sections": section_count,
                    "lod0_uv_channels": uv_count,
                    "used_material_slots": used_slots,
                    "material_paths": material_paths,
                    "bounds_origin": _vector_record(origin),
                    "bounds_extent": _vector_record(extent),
                    "raw_vertex_count": raw_vertex_count,
                    "raw_bounds": {
                        "min": [raw_bounds[0], raw_bounds[2], raw_bounds[4]],
                        "max": [raw_bounds[1], raw_bounds[3], raw_bounds[5]],
                    },
                }
                if vertex_count < 100 or triangle_count < 100:
                    part_failures.append(
                        "generated StaticMesh is empty/implausibly small "
                        f"(vertices={vertex_count}, triangles={triangle_count})"
                    )
                if section_count < 1:
                    part_failures.append("generated StaticMesh has no render section")
                if raw_vertex_count < 100:
                    part_failures.append("generated raw mesh has too few real vertices")
                if uv_count < int(manifest.get("uv_channel", -1)) + 1:
                    part_failures.append("generated StaticMesh lacks VAT UV channel")
                if len(used_slots) != len(source_used_slots):
                    part_failures.append(
                        "generated render section material count differs from source"
                    )
                extent_values = (float(extent.x), float(extent.y), float(extent.z))
                if not all(math.isfinite(value) and value > 1.0 for value in extent_values):
                    part_failures.append("generated StaticMesh has degenerate bounds")

                for slot, expected_material_path in expected_material_paths.items():
                    if slot >= len(static_materials):
                        part_failures.append(f"generated material slot {slot} is missing")
                        continue
                    material = static_materials[slot].material_interface
                    if _path(material) != expected_material_path:
                        part_failures.append(
                            f"slot {slot} does not use generated VAT material"
                        )
                        continue
                    if not isinstance(material, unreal.MaterialInstanceConstant):
                        part_failures.append(f"slot {slot} VAT material has wrong class")
                        continue
                    original = (
                        source_materials[slot].material_interface
                        if slot < len(source_materials)
                        else None
                    )
                    expected_parent = original
                    if original is None or not _material_has_bone_switch(original):
                        expected_parent = fallback_material
                    parent = material.get_editor_property("parent")
                    if _path(parent) != _path(expected_parent):
                        part_failures.append(f"slot {slot} VAT parent mismatch")
                    switch_enabled = (
                        unreal.MaterialEditingLibrary.get_material_instance_static_switch_parameter_value(
                            material,
                            unreal.Name("UseBoneAnimation"),
                            GLOBAL_ASSOCIATION,
                        )
                        if _material_has_bone_switch(material)
                        else False
                    )
                    if switch_enabled is not True:
                        part_failures.append(f"slot {slot} UseBoneAnimation is disabled")
                for slot, material_path in enumerate(material_paths):
                    if slot not in used_slots and material_path and not (
                        material_path.startswith(CITY_SAMPLE_ROOT)
                        or material_path.startswith(output_root + "/")
                    ):
                        part_failures.append(
                            f"unused slot {slot} has non-City-Sample material"
                        )

            generated_dependencies: dict[str, list[str]] = {}
            dependency_asset_paths = {
                key: _object_path(paths[key])
                for key in (
                    "static_mesh",
                    "animation_data",
                    "bone_position_texture",
                    "bone_rotation_texture",
                    "bone_weight_texture",
                )
            }
            dependency_asset_paths.update(
                {f"material_{slot}": path for slot, path in expected_material_paths.items()}
            )
            for key, asset_path in dependency_asset_paths.items():
                dependencies = _asset_dependencies(asset_path.split(".", 1)[0])
                generated_dependencies[key] = dependencies
                if any("/Mannequin/" in dependency for dependency in dependencies):
                    part_failures.append(f"{key} retains a Mannequin dependency")
                if any(_is_base_proxy_dependency(item) for item in dependencies):
                    part_failures.append(f"{key} depends on a *_base proxy")
                if key.startswith("material_"):
                    required_vat_packages = {
                        paths["bone_position_texture"],
                        paths["bone_rotation_texture"],
                        paths["bone_weight_texture"],
                    }
                    missing_vat_packages = required_vat_packages - set(dependencies)
                    if missing_vat_packages:
                        part_failures.append(
                            f"{key} is not wired to all part VAT textures: "
                            f"{sorted(missing_vat_packages)}"
                        )

            part_record = {
                "id": part_id,
                "role": role,
                "sources": {
                    "skeletal_mesh": source_mesh_path,
                    "animation": source_animation_path,
                    "declared_skeleton": declared_skeleton,
                    "skeletal_lod": skeletal_lod,
                    "lod_vertices": source_vertex_count,
                    "used_material_slots": source_used_slots,
                    "bounds": source_bounds_record,
                },
                "paths": {key: _object_path(value) if key != "material_prefix" else value for key, value in paths.items()},
                "data": data_detail,
                "geometry": geometry_detail,
                "direct_dependencies": generated_dependencies,
                "failures": part_failures,
            }
            part_records.append(part_record)
            _record_check(
                checks,
                f"part_{variant_id}_{part_id}",
                not part_failures,
                part_failures or "PASS",
            )

        if len(body_position_textures) != 1 or len(body_rotation_textures) != 1:
            assembly_failures.append("three SK_Base body parts do not share bone textures")
        if len(body_frame_ranges) != 1:
            assembly_failures.append("body part autoplay ranges are not identical")
        if len(body_bone_counts) != 1:
            assembly_failures.append("body part bone layouts are not identical")
        if roles == EXPECTED_PART_ROLES and len(raw_bounds_by_role) == 4:
            mins_x = [value[0] for value in raw_bounds_by_role.values()]
            maxs_x = [value[1] for value in raw_bounds_by_role.values()]
            mins_y = [value[2] for value in raw_bounds_by_role.values()]
            maxs_y = [value[3] for value in raw_bounds_by_role.values()]
            mins_z = [value[4] for value in raw_bounds_by_role.values()]
            maxs_z = [value[5] for value in raw_bounds_by_role.values()]
            assembly_bounds = {
                "min": [min(mins_x), min(mins_y), min(mins_z)],
                "max": [max(maxs_x), max(maxs_y), max(maxs_z)],
                "height": max(maxs_z) - min(mins_z),
            }
            if assembly_bounds["height"] < 145.0 or assembly_bounds["max"][2] < 150.0:
                assembly_failures.append("assembly does not cover a full-height human")
            if assembly_bounds["min"][2] > 5.0:
                assembly_failures.append("assembly does not reach the ground")
            head_bounds = raw_bounds_by_role["head"]
            if head_bounds[4] < 110.0 or head_bounds[5] < 150.0:
                assembly_failures.append("head part is not positioned on the body")
            ordered = [
                raw_bounds_by_role["shoes"],
                raw_bounds_by_role["legs"],
                raw_bounds_by_role["torso"],
                raw_bounds_by_role["head"],
            ]
            z_gaps = [ordered[index + 1][4] - ordered[index][5] for index in range(3)]
            if any(gap > 8.0 for gap in z_gaps):
                assembly_failures.append(f"assembly parts have visible Z gaps: {z_gaps}")
        else:
            assembly_bounds = {}
            assembly_failures.append("could not measure all four assembly parts")
        if total_vertices < 2500:
            assembly_failures.append(
                f"assembly geometry is implausibly small: vertices={total_vertices}"
            )

        assembly_record = {
            "id": variant_id,
            "animation": variant_animation_path,
            "target_frame_count": target_frame_count,
            "part_count": len(part_records),
            "total_lod0_vertices": total_vertices,
            "bounds": assembly_bounds,
            "body_bone_position_textures": sorted(body_position_textures),
            "body_bone_rotation_textures": sorted(body_rotation_textures),
            "parts": part_records,
            "failures": assembly_failures,
        }
        records.append(assembly_record)
        _record_check(
            checks,
            f"assembly_{variant_id}",
            not assembly_failures,
            assembly_failures or "PASS",
        )

    _record_check(
        checks,
        "twenty_four_visible_parts",
        configured_part_count == 24,
        configured_part_count,
    )
    registry = unreal.AssetRegistryHelpers.get_asset_registry()
    actual_assets = {
        f"{asset.package_name}.{asset.asset_name}"
        for asset in registry.get_assets_by_path(output_root, recursive=True)
    }
    _record_check(
        checks,
        "exact_generated_asset_set",
        actual_assets == expected_assets,
        {
            "expected_count": len(expected_assets),
            "actual_count": len(actual_assets),
            "missing": sorted(expected_assets - actual_assets),
            "unexpected": sorted(actual_assets - expected_assets),
        },
    )
    _record_check(
        checks,
        "no_mannequin_output_paths",
        all("mannequin" not in path.lower() for path in actual_assets),
        sorted(path for path in actual_assets if "mannequin" in path.lower()),
    )
    _record_check(
        checks,
        "no_base_proxy_output_paths",
        all("_base" not in path.lower() for path in actual_assets),
        sorted(path for path in actual_assets if "_base" in path.lower()),
    )

    passed = all(bool(check["passed"]) for check in checks)
    report = {
        "schema_version": 2,
        "status": "PASS" if passed else "FAIL",
        "engine_version": unreal.SystemLibrary.get_engine_version(),
        "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "read_only": True,
        "material_parameter_association": "GLOBAL_PARAMETER_ONLY",
        "checks": checks,
        "assemblies": records,
    }
    report_dir = Path(unreal.Paths.project_saved_dir()) / "Reports" / "OpenMassCrowd"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / "city_sample_vat_verify_latest.json"
    report_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + chr(10), encoding="utf-8"
    )
    print("OPEN_MASS_CITY_SAMPLE_VAT_VERIFY=" + json.dumps(report, sort_keys=True))
    if not passed:
        raise RuntimeError(f"City Sample VAT verification failed; report={report_path}")


if __name__ == "__main__":
    main()
