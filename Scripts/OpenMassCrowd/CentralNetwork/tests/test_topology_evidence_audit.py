from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT_DIR))

import audit_central_topology_evidence as audit  # noqa: E402


def feature(
    identifier: str,
    first_id: str,
    first: list[float],
    second_id: str,
    second: list[float],
    *,
    tags: dict[str, str] | None = None,
) -> dict:
    return {
        "feature_id": f"{identifier}-central-r0-c0-part-000",
        "parent_feature_id": identifier,
        "classification": "sidewalk",
        "tags": tags or {"highway": "footway", "footway": "sidewalk"},
        "points": [
            {"point_id": first_id, "unreal_position_cm": first},
            {"point_id": second_id, "unreal_position_cm": second},
        ],
    }


class TopologyEvidenceAuditTests(unittest.TestCase):
    def test_same_level_exact_intersection_is_discovered(self) -> None:
        first = feature("osm-way-a", "a0", [0.0, 0.0, 0.0], "a1", [10.0, 0.0, 0.0])
        second = feature("osm-way-b", "b0", [5.0, -5.0, 0.0], "b1", [5.0, 5.0, 0.0])
        components, node_component = audit._component_index([first, second])
        report = audit.source_intersection_audit([first, second], node_component)
        self.assertEqual(len(components), 2)
        self.assertEqual(report["cross_component_non_noded_intersection_count"], 1)
        self.assertEqual(report["same_level_ground_intersection_count"], 1)

    def test_cross_layer_exact_intersection_fails_closed(self) -> None:
        first = feature("osm-way-a", "a0", [0.0, 0.0, 0.0], "a1", [10.0, 0.0, 0.0])
        second = feature(
            "osm-way-b",
            "b0",
            [5.0, -5.0, 0.0],
            "b1",
            [5.0, 5.0, 0.0],
            tags={"highway": "footway", "bridge": "yes", "layer": "1"},
        )
        _components, node_component = audit._component_index([first, second])
        report = audit.source_intersection_audit([first, second], node_component)
        self.assertEqual(report["cross_component_non_noded_intersection_count"], 1)
        self.assertEqual(report["same_level_ground_intersection_count"], 0)

    def test_near_distinct_ids_remain_review_only(self) -> None:
        first = feature("osm-way-a", "a0", [0.0, 0.0, 0.0], "a1", [10.0, 0.0, 0.0])
        second = feature("osm-way-b", "b0", [10.2, 0.0, 0.0], "b1", [20.0, 0.0, 0.0])
        _components, node_component = audit._component_index([first, second])
        report = audit.projected_point_id_audit([first, second], node_component)
        self.assertEqual(report["strict_coordinate_alias_count"], 1)
        self.assertGreaterEqual(
            report["review_only_cross_component_near_point_pairs"]["within_20cm"], 1
        )

    def test_certified_height_delta_controls_recovery_eligibility(self) -> None:
        self.assertEqual(audit.run_self_test()["status"], "ok")

    def test_tunnel_without_layer_is_not_ground(self) -> None:
        candidate = {
            "classification": "footway",
            "tags": {"highway": "footway", "tunnel": "yes"},
        }
        self.assertFalse(audit._strict_structure_profile(candidate)["strict_ground"])


if __name__ == "__main__":
    unittest.main()
