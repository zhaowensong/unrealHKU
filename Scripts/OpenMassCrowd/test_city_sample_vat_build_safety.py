"""Host-only regression tests for City Sample VAT build helpers."""

from __future__ import annotations

import copy
import json
import runpy
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


SCRIPT_PATH = Path(__file__).with_name("build_city_sample_vat_assets.py")
MANIFEST_PATH = Path(__file__).with_name("city_sample_vat_manifest.json")


class FakeTexture2D:
    def __init__(self, path: str) -> None:
        self.path = path

    def get_path_name(self) -> str:
        return self.path


class UnloadedSoftTextureOwner:
    """Model UE 5.7 skipping a None write for an unloaded soft path."""

    def __init__(self) -> None:
        self.value: object = "serialized-but-unloaded-soft-path"
        self.loaded = False
        self.writes: list[object | None] = []

    def set_editor_property(self, _name: str, value: object | None) -> None:
        self.writes.append(value)
        if value is None and not self.loaded:
            return
        self.value = value
        self.loaded = value is not None

    def get_editor_property(self, _name: str) -> object | None:
        return self.value if self.loaded else None


class CitySampleVatBuildSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        global_association = object()
        cls.global_association = global_association
        cls.fake_unreal = SimpleNamespace(
            Texture2D=FakeTexture2D,
            MaterialParameterAssociation=SimpleNamespace(
                GLOBAL_PARAMETER=global_association
            ),
        )
        with patch.dict(sys.modules, {"unreal": cls.fake_unreal}):
            cls.module = runpy.run_path(
                str(SCRIPT_PATH), run_name="city_sample_vat_build_host_test"
            )

    def test_soft_texture_clear_primes_loaded_value_before_none(self) -> None:
        module = self.module

        owner = UnloadedSoftTextureOwner()
        sentinel = FakeTexture2D(
            "/Game/OpenMassCrowd/Test/TX_LoadedSentinel.TX_LoadedSentinel"
        )
        module["_clear_soft_texture_reference"](
            owner, "vertex_position_texture", sentinel
        )

        self.assertEqual(owner.writes, [sentinel, None])
        self.assertIsNone(owner.value)
        self.assertIs(module["GLOBAL_ASSOCIATION"], self.global_association)

    def test_manifest_declares_six_complete_real_geometry_assemblies(self) -> None:
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        variants = self.module["_validate_manifest"](manifest)

        self.assertEqual(manifest["schema_version"], 2)
        self.assertEqual(len(variants), 6)
        self.assertEqual(sum(len(item["parts"]) for item in variants), 24)
        for variant in variants:
            self.assertEqual(
                {part["role"] for part in variant["parts"]},
                {"torso", "legs", "shoes", "head"},
            )
            self.assertFalse(
                any(
                    "_base." in part["skeletal_mesh"].lower()
                    for part in variant["parts"]
                )
            )

    def test_body_parts_share_animation_textures_but_not_weights(self) -> None:
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        output_root = manifest["output_root"]
        for variant in manifest["variants"]:
            body_paths = [
                self.module["_part_paths"](
                    output_root,
                    variant["id"],
                    part["id"],
                    part["texture_group"],
                )
                for part in variant["parts"]
                if part["role"] != "head"
            ]
            self.assertEqual(
                len({item["bone_position_texture"] for item in body_paths}), 1
            )
            self.assertEqual(
                len({item["bone_rotation_texture"] for item in body_paths}), 1
            )
            self.assertEqual(
                len({item["bone_weight_texture"] for item in body_paths}), 3
            )

    def test_manifest_validator_rejects_base_proxy_regression(self) -> None:
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        broken = copy.deepcopy(manifest)
        broken["variants"][0]["parts"][0]["skeletal_mesh"] = (
            "/Game/CitySampleCrowd/Character/Female/NormalWeight/Meshes/"
            "f_tal_nrw_base.f_tal_nrw_base"
        )
        with self.assertRaises(self.module["BuildError"]):
            self.module["_validate_manifest"](broken)


if __name__ == "__main__":
    unittest.main()
