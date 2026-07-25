"""Bake six official City Sample modular people into Mass-compatible VAT parts.

City Sample's ``*_base`` meshes are deliberately tiny animation drivers.  A
visible crowd character is assembled from modular torso, legs, shoes and face
meshes, so this builder creates four real StaticMesh/VAT chains per variant.
The three body parts share the same SK_Base bone-position/rotation textures;
their bone-weight textures remain part-specific.  City Sample exposes no
visible SK_Base head, therefore the official FaceMesh is baked against the
compatible ``Crowd_Neutral`` face sequence with exactly the body walk frame
count.  Mass can consequently send one autoplay frame range and phase to all
four static-mesh refs in an assembly.

Run inside the already-open UE 5.7 editor through
``run_unreal_python_via_mcp.py``.  Source assets are never edited.  Generated
assets live only below ``/Game/OpenMassCrowd/CitySampleVAT``.  Obsolete assets
in that dedicated generated root are removed only after every new part bakes
successfully, preserving the previous output if a build fails midway.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import unreal


MANIFEST_RELATIVE_PATH = Path("Scripts/OpenMassCrowd/city_sample_vat_manifest.json")
SOURCE_DATA_ASSET = (
    "/AnimToTexture/Characters/Mannequin/Data/"
    "DA_BoneAnimation.DA_BoneAnimation"
)
SOURCE_TEXTURES = {
    "bone_position_texture": (
        "/AnimToTexture/Characters/Mannequin/Textures/BoneAnimation/"
        "TX_BonePosition.TX_BonePosition"
    ),
    "bone_rotation_texture": (
        "/AnimToTexture/Characters/Mannequin/Textures/BoneAnimation/"
        "TX_BoneRotation.TX_BoneRotation"
    ),
    "bone_weight_texture": (
        "/AnimToTexture/Characters/Mannequin/Textures/BoneAnimation/"
        "TX_BoneWeight.TX_BoneWeight"
    ),
}
EXPECTED_VARIANT_IDS = {"FTN", "FTO", "FTU", "MTN", "MTO", "MTU"}
EXPECTED_PART_ROLES = {"torso", "legs", "shoes", "head"}
EXPECTED_OUTPUT_ROOT = "/Game/OpenMassCrowd/CitySampleVAT"
CITY_SAMPLE_ROOT = "/Game/CitySampleCrowd/"
SK_BASE = "/Game/CitySampleCrowd/Character/Shared/Rig/SK_Base.SK_Base"
FACE_SKELETON = (
    "/Game/CitySampleCrowd/Character/Shared/Rig/"
    "Face_Archetype_Skeleton.Face_Archetype_Skeleton"
)
GLOBAL_ASSOCIATION = unreal.MaterialParameterAssociation.GLOBAL_PARAMETER
IDENTIFIER_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


class BuildError(RuntimeError):
    pass


def _object_path(package_path: str) -> str:
    asset_name = package_path.rsplit("/", 1)[-1]
    return f"{package_path}.{asset_name}"


def _validate_identifier(value: object, label: str) -> str:
    text = str(value)
    if not IDENTIFIER_RE.fullmatch(text):
        raise BuildError(f"invalid {label}: {text!r}")
    return text


def _part_paths(output_root: str, variant_id: str, part_id: str, group: str) -> dict:
    variant_root = f"{output_root}/{variant_id}"
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


def _validate_manifest(manifest: dict) -> list[dict]:
    if manifest.get("schema_version") != 2:
        raise BuildError("unsupported City Sample VAT manifest schema")
    if str(manifest.get("output_root", "")).rstrip("/") != EXPECTED_OUTPUT_ROOT:
        raise BuildError(f"VAT output root must be {EXPECTED_OUTPUT_ROOT}")
    sample_rate = float(manifest.get("sample_rate", 0.0))
    if not 1.0 <= sample_rate <= 120.0:
        raise BuildError("VAT sample rate must be in [1, 120]")
    uv_channel = int(manifest.get("uv_channel", -1))
    if uv_channel not in (2, 3):
        raise BuildError("City Sample VAT must avoid UV0 and generated UV1")

    variants = manifest.get("variants")
    if not isinstance(variants, list) or len(variants) != 6:
        raise BuildError("VAT manifest must contain exactly six assemblies")
    variant_ids = [_validate_identifier(item.get("id", ""), "variant id") for item in variants]
    if set(variant_ids) != EXPECTED_VARIANT_IDS or len(set(variant_ids)) != 6:
        raise BuildError("VAT variant ids must be exactly FTN/FTO/FTU/MTN/MTO/MTU")

    for spec in variants:
        variant_id = str(spec["id"])
        animation_path = str(spec.get("animation", ""))
        if not animation_path.startswith(CITY_SAMPLE_ROOT) or "_Walk_F" not in animation_path:
            raise BuildError(f"{variant_id} must use an official City Sample walk")
        parts = spec.get("parts")
        if not isinstance(parts, list) or len(parts) != 4:
            raise BuildError(f"{variant_id} must have torso/legs/shoes/head parts")
        roles = {str(part.get("role", "")) for part in parts}
        if roles != EXPECTED_PART_ROLES:
            raise BuildError(f"{variant_id} part roles are incomplete: {sorted(roles)}")
        part_ids = [_validate_identifier(part.get("id", ""), "part id") for part in parts]
        if len(set(part_ids)) != len(part_ids):
            raise BuildError(f"{variant_id} has duplicate part ids")

        for part in parts:
            part_id = str(part["id"])
            role = str(part["role"])
            mesh_path = str(part.get("skeletal_mesh", ""))
            declared_skeleton = str(part.get("skeleton", ""))
            part_animation = str(part.get("animation", animation_path))
            texture_group = _validate_identifier(
                part.get("texture_group", ""), "texture group"
            )
            precision = str(part.get("precision", ""))
            if not mesh_path.startswith(CITY_SAMPLE_ROOT):
                raise BuildError(f"{variant_id}/{part_id} is not City Sample content")
            if "_base." in mesh_path.lower():
                raise BuildError(f"{variant_id}/{part_id} selects a forbidden *_base proxy")
            if not part_animation.startswith(CITY_SAMPLE_ROOT):
                raise BuildError(f"{variant_id}/{part_id} animation is not City Sample")
            if int(part.get("skeletal_lod", -1)) < 0:
                raise BuildError(f"{variant_id}/{part_id} requires an explicit LOD")
            if precision not in ("eight_bits", "sixteen_bits"):
                raise BuildError(f"{variant_id}/{part_id} has invalid precision")
            if role == "head":
                if declared_skeleton != FACE_SKELETON or texture_group != "Head":
                    raise BuildError(f"{variant_id}/{part_id} must be the Face VAT group")
                fallback = str(part.get("material_fallback", ""))
                if not fallback.startswith(CITY_SAMPLE_ROOT):
                    raise BuildError(f"{variant_id}/{part_id} needs a City Sample fallback material")
                if precision != "sixteen_bits":
                    raise BuildError(f"{variant_id}/{part_id} needs 16-bit bone indices")
            else:
                if declared_skeleton != SK_BASE or texture_group != "Body":
                    raise BuildError(f"{variant_id}/{part_id} must share the SK_Base Body group")
                if part_animation != animation_path:
                    raise BuildError(f"{variant_id}/{part_id} must share the walk sequence")
                if precision != "eight_bits":
                    raise BuildError(f"{variant_id}/{part_id} SK_Base parts use 8-bit precision")
    return variants


def _load_required(path: str, expected_type: type | None = None) -> object:
    asset = unreal.load_asset(path)
    if asset is None:
        raise BuildError(f"missing asset: {path}")
    if expected_type is not None and not isinstance(asset, expected_type):
        raise BuildError(f"wrong asset class: {path}: {asset.get_class().get_name()}")
    return asset


def _load_or_duplicate(source: str, destination: str) -> object:
    if unreal.EditorAssetLibrary.does_asset_exist(destination):
        return _load_required(destination)
    duplicated = unreal.EditorAssetLibrary.duplicate_asset(source, destination)
    if duplicated is None:
        raise BuildError(f"failed to duplicate {source} -> {destination}")
    return duplicated


def _enum_value(enum_type: type, preferred: str, fallback: str) -> object:
    value = getattr(enum_type, preferred, None)
    if value is None:
        value = getattr(enum_type, fallback, None)
    if value is None:
        raise BuildError(f"missing enum value {enum_type}.{preferred}")
    return value


def _precision_value(name: str) -> object:
    if name == "eight_bits":
        return _enum_value(unreal.AnimToTexturePrecision, "EIGHT_BITS", "EIGHTBITS")
    if name == "sixteen_bits":
        return _enum_value(
            unreal.AnimToTexturePrecision, "SIXTEEN_BITS", "SIXTEENBITS"
        )
    raise BuildError(f"unsupported precision: {name}")


def _set_material(static_mesh: object, slot_index: int, material: object) -> None:
    try:
        static_mesh.set_material(slot_index, material)
        return
    except Exception:
        pass
    subsystem = unreal.get_editor_subsystem(unreal.StaticMeshEditorSubsystem)
    setter = getattr(subsystem, "set_material", None)
    if setter is None:
        raise BuildError("UE Python exposes no StaticMesh material setter")
    setter(static_mesh, slot_index, material)


def _material_has_bone_switch(material: object) -> bool:
    return "UseBoneAnimation" in {
        str(name)
        for name in unreal.MaterialEditingLibrary.get_static_switch_parameter_names(
            material
        )
    }


def _enable_static_switch(material: object, parameter_name: str) -> None:
    if not _material_has_bone_switch(material):
        raise BuildError(
            f"material does not expose static switch {parameter_name}: "
            f"{material.get_path_name()}"
        )
    unreal.MaterialEditingLibrary.set_material_instance_static_switch_parameter_value(
        material, unreal.Name(parameter_name), True, GLOBAL_ASSOCIATION
    )
    enabled = unreal.MaterialEditingLibrary.get_material_instance_static_switch_parameter_value(
        material, unreal.Name(parameter_name), GLOBAL_ASSOCIATION
    )
    if enabled is not True:
        raise BuildError(
            f"failed to enable static switch {parameter_name}: "
            f"{material.get_path_name()}"
        )


def _save(asset: object) -> None:
    if not unreal.EditorAssetLibrary.save_loaded_asset(asset, only_if_is_dirty=False):
        raise BuildError(f"failed to save {asset.get_path_name()}")


def _clear_soft_texture_reference(
    owner: object, property_name: str, loaded_sentinel: object
) -> None:
    """Reliably clear a ``TSoftObjectPtr<UTexture2D>`` through UE 5.7 Python."""
    if not isinstance(loaded_sentinel, unreal.Texture2D):
        raise BuildError(
            f"soft-reference sentinel is not a Texture2D: {property_name}"
        )

    owner.set_editor_property(property_name, loaded_sentinel)
    assigned = owner.get_editor_property(property_name)
    assigned_path = assigned.get_path_name() if assigned is not None else "None"
    sentinel_path = loaded_sentinel.get_path_name()
    if assigned_path != sentinel_path:
        raise BuildError(
            f"failed to prime soft texture reference {property_name}: "
            f"expected={sentinel_path} read_back={assigned_path}"
        )

    owner.set_editor_property(property_name, None)
    if owner.get_editor_property(property_name) is not None:
        raise BuildError(f"failed to clear soft texture reference {property_name}")


def _animation_key_count(animation: object) -> int:
    count = int(unreal.AnimationLibrary.get_num_keys(animation))
    if count < 2:
        raise BuildError(f"animation has fewer than two keys: {animation.get_path_name()}")
    return count


def _source_skeleton_path(skeletal_mesh: object) -> str:
    skeleton = skeletal_mesh.get_editor_property("skeleton")
    return skeleton.get_path_name() if skeleton is not None else ""


def _prepare_vat_uv_channel(static_mesh: object, uv_channel: int) -> None:
    """Reserve ``uv_channel`` even when converter-selected lightmap UVs vary.

    ``ConvertMeshesToStaticMesh`` chooses the destination lightmap channel from
    the source mesh.  Single-material parts commonly get UV1 while multi-slot
    parts get UV2, making a fixed VAT UV2 fail on only some modular parts.  VAT
    crowd instances use movable lighting, so disable generated static-lightmap
    UVs, point the dormant lightmap coordinate at UV1, then pad only the missing
    channels.  AnimToTexture can now insert/overwrite the requested VAT channel
    deterministically for every part.
    """
    if not unreal.AnimToTextureBPLibrary.set_light_map_index(
        static_mesh, 0, 1, False
    ):
        raise BuildError(
            f"failed to disable generated lightmap UVs: {static_mesh.get_path_name()}"
        )
    subsystem = unreal.get_editor_subsystem(unreal.StaticMeshEditorSubsystem)
    channel_count = int(subsystem.get_num_uv_channels(static_mesh, 0))
    while channel_count < uv_channel:
        if not subsystem.add_uv_channel(static_mesh, 0):
            raise BuildError(
                f"failed to pad UV channels for {static_mesh.get_path_name()}: "
                f"current={channel_count} target={uv_channel}"
            )
        channel_count = int(subsystem.get_num_uv_channels(static_mesh, 0))
    if channel_count < uv_channel:
        raise BuildError(
            f"VAT UV channel remains out of range: current={channel_count} "
            f"target={uv_channel}"
        )


def _build_part(
    output_root: str,
    sample_rate: float,
    uv_channel: int,
    variant_id: str,
    variant_animation_path: str,
    target_frame_count: int,
    spec: dict,
) -> tuple[dict, set[str]]:
    part_id = str(spec["id"])
    role = str(spec["role"])
    texture_group = str(spec["texture_group"])
    paths = _part_paths(output_root, variant_id, part_id, texture_group)
    skeletal_lod = int(spec["skeletal_lod"])
    animation_path = str(spec.get("animation", variant_animation_path))

    skeletal_mesh = _load_required(spec["skeletal_mesh"], unreal.SkeletalMesh)
    animation = _load_required(animation_path, unreal.AnimSequence)
    actual_skeleton = _source_skeleton_path(skeletal_mesh)
    if actual_skeleton != str(spec["skeleton"]):
        raise BuildError(
            f"{variant_id}/{part_id} skeleton mismatch: "
            f"manifest={spec['skeleton']} actual={actual_skeleton}"
        )
    if _animation_key_count(animation) < target_frame_count:
        raise BuildError(
            f"{variant_id}/{part_id} animation is too short for synchronized "
            f"frame count {target_frame_count}"
        )

    skeletal_subsystem = unreal.get_editor_subsystem(
        unreal.SkeletalMeshEditorSubsystem
    )
    lod_count = int(skeletal_subsystem.get_lod_count(skeletal_mesh))
    if skeletal_lod >= lod_count:
        raise BuildError(
            f"{variant_id}/{part_id} LOD {skeletal_lod} >= {lod_count}"
        )
    source_vertex_count = int(
        skeletal_subsystem.get_num_verts(skeletal_mesh, skeletal_lod)
    )
    if source_vertex_count < 100:
        raise BuildError(
            f"{variant_id}/{part_id} source LOD is not real visible geometry: "
            f"vertices={source_vertex_count}"
        )

    static_mesh = unreal.AnimToTextureBPLibrary.convert_skeletal_mesh_to_static_mesh(
        skeletal_mesh, paths["static_mesh"], skeletal_lod
    )
    if static_mesh is None:
        raise BuildError(f"failed to convert skeletal mesh for {variant_id}/{part_id}")
    _prepare_vat_uv_channel(static_mesh, uv_channel)

    data_asset = _load_or_duplicate(SOURCE_DATA_ASSET, paths["animation_data"])
    if not isinstance(data_asset, unreal.AnimToTextureDataAsset):
        raise BuildError(f"generated data asset has wrong type: {paths['animation_data']}")

    textures = {}
    for property_name, source_path in SOURCE_TEXTURES.items():
        destination = paths[property_name]
        texture = _load_or_duplicate(source_path, destination)
        if not isinstance(texture, unreal.Texture2D):
            raise BuildError(f"generated texture has wrong type: {destination}")
        textures[property_name] = texture

    sequence_info = unreal.AnimToTextureAnimSequenceInfo()
    sequence_info.set_editor_property("enabled", True)
    sequence_info.set_editor_property("anim_sequence", animation)
    # Force every part in an assembly to exactly the body's autoplay range.
    sequence_info.set_editor_property("use_custom_range", True)
    sequence_info.set_editor_property("start_frame", 0)
    sequence_info.set_editor_property("end_frame", target_frame_count - 1)

    data_asset.set_editor_property("skeletal_mesh", skeletal_mesh)
    data_asset.set_editor_property("skeletal_lod_index", skeletal_lod)
    data_asset.set_editor_property("static_mesh", static_mesh)
    data_asset.set_editor_property("static_lod_index", 0)
    data_asset.set_editor_property("uv_channel", uv_channel)
    data_asset.set_editor_property("num_driver_triangles", 4)
    data_asset.set_editor_property("sigma", 1.0)
    data_asset.set_editor_property("max_height", 4096)
    data_asset.set_editor_property("max_width", 4096)
    data_asset.set_editor_property("enforce_power_of_two", False)
    data_asset.set_editor_property("precision", _precision_value(str(spec["precision"])))
    data_asset.set_editor_property(
        "mode", _enum_value(unreal.AnimToTextureMode, "BONE", "BONE_ANIMATION")
    )
    _clear_soft_texture_reference(
        data_asset, "vertex_position_texture", textures["bone_position_texture"]
    )
    _clear_soft_texture_reference(
        data_asset, "vertex_normal_texture", textures["bone_rotation_texture"]
    )
    data_asset.set_editor_property(
        "num_bone_influences",
        _enum_value(
            unreal.AnimToTextureNumBoneInfluences, "FOUR", "FOUR_INFLUENCES"
        ),
    )
    data_asset.set_editor_property("root_transform", unreal.Transform())
    data_asset.set_editor_property("attach_to_socket", unreal.Name("None"))
    data_asset.set_editor_property("sample_rate", float(sample_rate))
    data_asset.set_editor_property("anim_sequences", [sequence_info])
    data_asset.set_editor_property("auto_play", True)
    data_asset.set_editor_property("animation_index", 0)
    for property_name, texture in textures.items():
        data_asset.set_editor_property(property_name, texture)

    if not unreal.AnimToTextureBPLibrary.animation_to_texture(data_asset):
        raise BuildError(f"AnimToTexture bake failed for {variant_id}/{part_id}")

    _clear_soft_texture_reference(
        data_asset, "vertex_position_texture", textures["bone_position_texture"]
    )
    _clear_soft_texture_reference(
        data_asset, "vertex_normal_texture", textures["bone_rotation_texture"]
    )

    static_subsystem = unreal.get_editor_subsystem(unreal.StaticMeshEditorSubsystem)
    section_count = int(static_mesh.get_num_sections(0))
    used_slots = sorted(
        {
            int(static_subsystem.get_lod_material_slot(static_mesh, 0, section))
            for section in range(section_count)
        }
    )
    static_materials = list(static_mesh.get_editor_property("static_materials"))
    if not used_slots or any(slot < 0 or slot >= len(static_materials) for slot in used_slots):
        raise BuildError(
            f"{variant_id}/{part_id} has invalid used material slots: {used_slots}"
        )

    fallback_material = None
    fallback_path = str(spec.get("material_fallback", ""))
    if fallback_path:
        fallback_material = _load_required(
            fallback_path, unreal.MaterialInstanceConstant
        )
        if not _material_has_bone_switch(fallback_material):
            raise BuildError(
                f"fallback lacks UseBoneAnimation: {fallback_material.get_path_name()}"
            )

    material_records = []
    generated_materials = []
    for slot_index in used_slots:
        original_material = static_materials[slot_index].material_interface
        if original_material is None:
            raise BuildError(
                f"{variant_id}/{part_id} used material slot {slot_index} is empty"
            )
        source_material = original_material
        used_fallback = False
        if not _material_has_bone_switch(source_material):
            if fallback_material is None:
                raise BuildError(
                    f"{variant_id}/{part_id} material lacks UseBoneAnimation and "
                    f"has no fallback: {source_material.get_path_name()}"
                )
            source_material = fallback_material
            used_fallback = True
        if not isinstance(source_material, unreal.MaterialInstanceConstant):
            raise BuildError(
                f"VAT material source is not a material instance: "
                f"{source_material.get_path_name()}"
            )

        generated_path = f"{paths['material_prefix']}{slot_index}_VAT"
        material = _load_or_duplicate(source_material.get_path_name(), generated_path)
        if not isinstance(material, unreal.MaterialInstanceConstant):
            raise BuildError(f"generated material has wrong type: {generated_path}")
        material.set_editor_property("parent", source_material)
        _enable_static_switch(material, "UseBoneAnimation")
        unreal.AnimToTextureBPLibrary.update_material_instance_from_data_asset(
            data_asset, material
        )
        _set_material(static_mesh, slot_index, material)
        generated_materials.append(material)
        material_records.append(
            {
                "slot": slot_index,
                "original_source": original_material.get_path_name(),
                "vat_parent": source_material.get_path_name(),
                "used_fallback": used_fallback,
                "generated": material.get_path_name(),
            }
        )

    for asset in (*textures.values(), *generated_materials, static_mesh, data_asset):
        _save(asset)

    animation_index = data_asset.get_index_from_anim_sequence(animation)
    num_frames = int(data_asset.get_editor_property("num_frames"))
    num_bones = int(data_asset.get_editor_property("num_bones"))
    if animation_index != 0 or num_frames != target_frame_count or num_bones < 1:
        raise BuildError(
            f"invalid VAT result for {variant_id}/{part_id}: "
            f"index={animation_index} frames={num_frames}/{target_frame_count} "
            f"bones={num_bones}"
        )

    expected_assets = {
        _object_path(paths["static_mesh"]),
        _object_path(paths["animation_data"]),
        *(_object_path(paths[name]) for name in SOURCE_TEXTURES),
        *(record["generated"] for record in material_records),
    }
    result = {
        "id": part_id,
        "role": role,
        "source_skeletal_mesh": str(spec["skeletal_mesh"]),
        "source_skeleton": actual_skeleton,
        "source_skeletal_lod": skeletal_lod,
        "source_lod_vertices": source_vertex_count,
        "source_animation": animation_path,
        "precision": str(spec["precision"]),
        "texture_group": texture_group,
        "static_mesh": static_mesh.get_path_name(),
        "animation_data": data_asset.get_path_name(),
        "animation_index": animation_index,
        "num_frames": num_frames,
        "num_bones": num_bones,
        "sample_rate": float(data_asset.get_editor_property("sample_rate")),
        "uv_channel": int(data_asset.get_editor_property("uv_channel")),
        "bone_position_texture": textures["bone_position_texture"].get_path_name(),
        "bone_rotation_texture": textures["bone_rotation_texture"].get_path_name(),
        "bone_weight_texture": textures["bone_weight_texture"].get_path_name(),
        "vertex_position_texture": "",
        "vertex_normal_texture": "",
        "used_material_slots": used_slots,
        "materials": material_records,
    }
    return result, expected_assets


def _cleanup_obsolete_generated_assets(output_root: str, expected: set[str]) -> list[str]:
    actual = {
        str(path)
        for path in unreal.EditorAssetLibrary.list_assets(
            output_root, recursive=True, include_folder=False
        )
    }
    obsolete = sorted(actual - expected)
    for asset_path in obsolete:
        if not unreal.EditorAssetLibrary.delete_asset(asset_path):
            raise BuildError(f"failed to delete obsolete generated asset: {asset_path}")
    return obsolete


def main() -> None:
    manifest_path = Path(unreal.Paths.project_dir()) / MANIFEST_RELATIVE_PATH
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes.decode("utf-8"))
    variants = _validate_manifest(manifest)

    output_root = str(manifest["output_root"]).rstrip("/")
    sample_rate = float(manifest["sample_rate"])
    uv_channel = int(manifest["uv_channel"])
    results = []
    expected_assets: set[str] = set()
    for spec in variants:
        variant_id = str(spec["id"])
        variant_animation_path = str(spec["animation"])
        variant_animation = _load_required(
            variant_animation_path, unreal.AnimSequence
        )
        target_frame_count = _animation_key_count(variant_animation)
        unreal.log_warning(
            "OPEN_MASS_VAT_ASSEMBLY_BEGIN "
            f"id={variant_id} frames={target_frame_count}"
        )
        part_results = []
        for part_spec in spec["parts"]:
            unreal.log_warning(
                "OPEN_MASS_VAT_PART_BEGIN "
                f"id={variant_id} part={part_spec['id']} role={part_spec['role']}"
            )
            part_result, part_assets = _build_part(
                output_root,
                sample_rate,
                uv_channel,
                variant_id,
                variant_animation_path,
                target_frame_count,
                part_spec,
            )
            part_results.append(part_result)
            expected_assets.update(part_assets)
            unreal.log_warning(
                "OPEN_MASS_VAT_PART_READY "
                f"id={variant_id} part={part_result['id']} "
                f"vertices={part_result['source_lod_vertices']} "
                f"frames={part_result['num_frames']} bones={part_result['num_bones']}"
            )
        results.append(
            {
                "id": variant_id,
                "animation": variant_animation_path,
                "target_frame_count": target_frame_count,
                "part_count": len(part_results),
                "parts": part_results,
            }
        )

    removed_assets = _cleanup_obsolete_generated_assets(output_root, expected_assets)
    report = {
        "schema_version": 2,
        "status": "PASS",
        "engine_version": unreal.SystemLibrary.get_engine_version(),
        "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "output_root": output_root,
        "variant_count": len(results),
        "part_count": sum(item["part_count"] for item in results),
        "expected_asset_count": len(expected_assets),
        "removed_obsolete_assets": removed_assets,
        "variants": results,
    }
    report_dir = Path(unreal.Paths.project_saved_dir()) / "Reports" / "OpenMassCrowd"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / "city_sample_vat_build_latest.json"
    report_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + chr(10), encoding="utf-8"
    )
    print("OPEN_MASS_CITY_SAMPLE_VAT_BUILD=" + json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
