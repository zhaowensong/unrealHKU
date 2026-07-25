"""Read-only parameter audit for the UE AnimToTexture and City Sample materials."""

from __future__ import annotations

import json

import unreal


MATERIAL_PATHS = (
    "/AnimToTexture/Characters/Mannequin/Materials/BoneAnimation/M_Body_BoneAnimation.M_Body_BoneAnimation",
    "/AnimToTexture/Characters/Mannequin/Materials/BoneAnimation/MI_Body_BoneAnimation.MI_Body_BoneAnimation",
    "/Game/CitySampleCrowd/Character/Shared/Materials/Clothing/M_Clothing.M_Clothing",
    "/Game/CitySampleCrowd/Character/Male/NormalWeight/Materials/M_BodySynthesized.M_BodySynthesized",
)


def _names(method_name: str, material: object) -> list[str]:
    method = getattr(unreal.MaterialEditingLibrary, method_name, None)
    if method is None:
        return []
    try:
        return sorted(str(name) for name in method(material))
    except Exception:
        return []


def main() -> None:
    records = []
    for path in MATERIAL_PATHS:
        material = unreal.load_asset(path)
        record = {
            "path": path,
            "loaded": material is not None,
            "class": material.get_class().get_name() if material else "",
        }
        if material:
            record["scalar_parameters"] = _names("get_scalar_parameter_names", material)
            record["vector_parameters"] = _names("get_vector_parameter_names", material)
            record["texture_parameters"] = _names("get_texture_parameter_names", material)
            record["static_switch_parameters"] = _names(
                "get_static_switch_parameter_names", material
            )
            try:
                parent = material.get_editor_property("parent")
                record["parent"] = parent.get_path_name() if parent else ""
            except Exception:
                record["parent"] = ""
        records.append(record)
    print("OPEN_MASS_VAT_MATERIAL_AUDIT=" + json.dumps(records, sort_keys=True))


if __name__ == "__main__":
    main()
