from __future__ import annotations

import sys
import unittest
from pathlib import Path

CENTRAL = Path(__file__).resolve().parents[1]
if str(CENTRAL) not in sys.path:
    sys.path.insert(0, str(CENTRAL))

import central_certified_import_common as common
import derive_central_ground_only_network as derive
import verify_central_ground_only_network as verify


def load(name: str):
    return common.load_json_strict(CENTRAL / name)


class CentralGroundOnlyTests(unittest.TestCase):
    def test_semantic_filter_rejects_elevated_steps_and_tunnel(self):
        policy = load("central_ground_only_policy.json")
        cases = (
            ({"feature_id": "a", "tags": {"bridge": "yes"}}, "bridge:yes"),
            ({"feature_id": "b", "tags": {"bridge": "viaduct"}}, "bridge:viaduct"),
            ({"feature_id": "c", "tags": {"layer": "-1"}}, "layer:-1"),
            ({"feature_id": "d", "tags": {"highway": "steps"}}, "highway:steps"),
            ({"feature_id": "e", "tags": {"tunnel": "yes"}}, "tunnel:yes"),
        )
        for feature, expected in cases:
            self.assertIn(expected, derive.feature_exclusion_reasons(feature, policy))
        self.assertEqual(
            derive.feature_exclusion_reasons(
                {"feature_id": "ground", "tags": {"highway": "footway", "layer": "0"}},
                policy,
            ),
            [],
        )

    def test_checked_in_cache_is_exact_deterministic_derivation(self):
        cache_path = CENTRAL / "Data/central_network_ground_only.json"
        parent_path = CENTRAL / "Data/central_network_certified.json"
        source_path = CENTRAL / "Data/central_pedestrian_source.json"
        policy_path = CENTRAL / "central_ground_only_policy.json"
        document = common.load_json_strict(cache_path)
        metrics = verify.validate_ground_only_document(
            document,
            common.load_json_strict(parent_path),
            common.load_json_strict(source_path),
            common.load_json_strict(policy_path),
            parent_path,
            policy_path,
            common.load_json_strict(CENTRAL / "central_network_certified.schema.json"),
        )
        self.assertEqual(metrics["lane_count"], 562)
        self.assertEqual(metrics["component_count"], 49)
        self.assertEqual(metrics["target_population"], 100)
        self.assertEqual(
            [row["target_population"] for row in document["spawn_districts"]],
            [17, 17, 17, 17, 16, 16],
        )
        self.assertEqual(metrics["excluded_directional_lane_count"], 256)
        self.assertTrue(metrics["zero_elevated_source_lanes"])

    def test_missing_ground_only_bit_fails_closed(self):
        document = load("Data/central_network_ground_only.json")
        document["cells"][0]["directed_lanes"][0]["ground_only_eligible"] = False
        with self.assertRaises(common.CertifiedImportError):
            verify.validate_ground_only_document(
                document,
                load("Data/central_network_certified.json"),
                load("Data/central_pedestrian_source.json"),
                load("central_ground_only_policy.json"),
                CENTRAL / "Data/central_network_certified.json",
                CENTRAL / "central_ground_only_policy.json",
                load("central_network_certified.schema.json"),
            )


if __name__ == "__main__":
    unittest.main()
