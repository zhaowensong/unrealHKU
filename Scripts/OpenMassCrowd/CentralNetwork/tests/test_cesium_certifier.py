"""Host regression tests for the fail-closed Central Cesium certifier."""

from __future__ import annotations

import copy
import importlib.util
import json
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPT_DIR = Path(__file__).resolve().parents[1]
CERTIFIER_PATH = SCRIPT_DIR / "certify_central_network_with_cesium.py"


def load_module(path: Path, name: str):
    specification = importlib.util.spec_from_file_location(name, path)
    if specification is None or specification.loader is None:
        raise RuntimeError("could not load {}".format(path))
    module = importlib.util.module_from_spec(specification)
    sys.modules[name] = module
    specification.loader.exec_module(module)
    return module


certifier = load_module(CERTIFIER_PATH, "central_cesium_certifier_under_test")


class CentralCesiumCertifierTests(unittest.TestCase):
    @staticmethod
    def _work_through_semantic_round(work, maximum_round):
        filtered = copy.deepcopy(work)
        all_semantic = filtered.get("semantic_recovery_candidates", [])
        kept = [
            candidate
            for candidate in all_semantic
            if int(candidate.get("tags", {}).get("semantic_round", 0))
            <= maximum_round
        ]
        removed_ids = {
            candidate["candidate_id"]
            for candidate in all_semantic
            if candidate not in kept
        }
        filtered["results"] = {
            candidate_id: result
            for candidate_id, result in filtered["results"].items()
            if candidate_id not in removed_ids
        }
        filtered["semantic_recovery_candidates"] = kept
        filtered["semantic_recovery_rounds"] = [
            round_record
            for round_record in filtered.get("semantic_recovery_rounds", [])
            if int(round_record["round"]) <= maximum_round
        ]
        filtered["semantic_recovery_round_count"] = maximum_round
        filtered.pop("semantic_recovery_planner", None)
        filtered["complete"] = False
        return filtered

    @staticmethod
    def _accepted_edge(candidate_id, first_node, second_node, first, second):
        candidate = {
            "candidate_id": candidate_id,
            "from_point_id": first_node,
            "to_point_id": second_node,
            "source_cell_id": "unused-in-test",
        }
        result = {
            "candidate_id": candidate_id,
            "status": "accepted",
            "from_point_id": first_node,
            "to_point_id": second_node,
            "samples": [
                {"center_position": list(first)},
                {"center_position": list(second)},
            ],
        }
        return candidate, result

    def test_real_source_dry_run_is_six_cell_and_review_gated(self):
        before = certifier.FINAL_PATH.read_bytes() if certifier.FINAL_PATH.exists() else None
        report = certifier.current_dry_run()
        after = certifier.FINAL_PATH.read_bytes() if certifier.FINAL_PATH.exists() else None

        self.assertEqual(report["primary_component"]["cell_ids"], [
            "central-r0-c0",
            "central-r0-c1",
            "central-r0-c2",
            "central-r1-c0",
            "central-r1-c1",
            "central-r1-c2",
        ])
        self.assertEqual(report["manual_review_blocked_count"], 4)
        self.assertEqual(report["support_spacing_cm"], 10.0)
        self.assertFalse(report["would_write_final"])
        self.assertEqual(before, after)

    def test_end_to_end_synthetic_cache_passes_all_strict_checks(self):
        report = certifier.run_self_test()
        self.assertTrue(report["passed"])
        self.assertEqual(report["cell_count"], 6)
        self.assertEqual(report["district_count"], 6)
        self.assertEqual(report["population"], 300)
        self.assertGreaterEqual(report["junction_count"], 8)
        self.assertGreaterEqual(report["cycle_rank"], 4)
        self.assertEqual(report["verifier_passed_checks"], 12)
        self.assertTrue(report["manual_review_blocked"])

    def test_incompatible_resume_cache_is_rejected_without_overwrite(self):
        projected, source, corrections = certifier.make_synthetic_source()
        certifier.validate_inputs(projected, source, corrections)
        plan = certifier.build_source_plan(projected, corrections)
        settings = copy.deepcopy(certifier.DEFAULT_SETTINGS)
        hashes = certifier.runtime_compatibility_hashes(settings)

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            projected_path = root / "projected.json"
            corrections_path = root / "corrections.json"
            work_path = root / "working.json"
            projected_path.write_text(json.dumps(projected), encoding="utf-8")
            corrections_path.write_text(json.dumps(corrections), encoding="utf-8")
            original = certifier.validate_or_create_work(
                plan,
                projected_path,
                corrections_path,
                settings,
                hashes,
                work_path,
            )
            original_bytes = work_path.read_bytes()
            changed_hashes = dict(hashes)
            changed_hashes["tileset_sha256"] = "f" * 64

            with self.assertRaises(certifier.CertificationError):
                certifier.validate_or_create_work(
                    plan,
                    projected_path,
                    corrections_path,
                    settings,
                    changed_hashes,
                    work_path,
                )

            self.assertEqual(work_path.read_bytes(), original_bytes)
            self.assertFalse(original["complete"])

    def test_external_verifier_is_atomic_promotion_gate(self):
        valid, _generated_audit, settings, _plan = (
            certifier.build_synthetic_certified_fixture()
        )
        settings["host_python"] = sys.executable

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            pending = root / "pending.json"
            final = root / "final.json"
            audit = root / "audit.json"
            report = certifier.promote_with_external_verifier(
                valid,
                {"test": "valid"},
                settings,
                pending_path=pending,
                final_path=final,
                audit_path=audit,
                source_path=None,
            )
            self.assertTrue(report["overall_passed"])
            valid_final = final.read_bytes()
            self.assertFalse(pending.exists())

            tampered = copy.deepcopy(valid)
            tampered["cells"][0]["directed_lanes"][0]["length_cm"] += 1.0
            with self.assertRaises(certifier.CertificationError):
                certifier.promote_with_external_verifier(
                    tampered,
                    {"test": "tampered"},
                    settings,
                    pending_path=pending,
                    final_path=final,
                    audit_path=audit,
                    source_path=None,
                )
            self.assertEqual(final.read_bytes(), valid_final)
            self.assertTrue(pending.exists())

    def test_real_work_cannot_promote_disconnected_component_union(self):
        projected = certifier.read_json(certifier.PROJECTED_SOURCE_PATH)
        source = certifier.read_json(certifier.SOURCE_PATH)
        corrections = certifier.read_json(certifier.CORRECTIONS_PATH)
        plan = certifier.build_source_plan(projected, corrections)
        work = certifier.read_json(certifier.WORK_PATH)
        settings = copy.deepcopy(certifier.DEFAULT_SETTINGS)
        del settings
        # This is a read-only regression test over the real working snapshot.
        # Calling the live augmentation helpers here may enqueue new recovery
        # candidates and write them into the user's cache while merely testing.
        plan["candidates"].extend(
            copy.deepcopy(work.get("generated_connector_candidates", []))
        )
        semantic = copy.deepcopy(work.get("semantic_recovery_candidates", []))
        certifier._register_semantic_nodes(plan, semantic)
        plan["candidates"].extend(semantic)

        with self.assertRaisesRegex(
            certifier.CertificationError,
            "no single trusted certified component independently satisfies",
        ):
            certifier.build_certified_document(
                plan,
                work["results"],
                projected,
                source,
                {
                    "georeference_sha256": projected["hashes"]["georeference_sha256"],
                    "tileset_sha256": "0" * 64,
                    "collision_settings_sha256": "1" * 64,
                },
            )

    def test_atomic_checkpoint_retries_transient_permission_error(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            output = Path(temporary_directory) / "checkpoint.json"
            real_replace = certifier.os.replace
            attempts = []

            def flaky_replace(source, destination):
                attempts.append((source, destination))
                if len(attempts) == 1:
                    raise PermissionError("transient sharing violation")
                return real_replace(source, destination)

            with mock.patch.object(
                certifier.os, "replace", side_effect=flaky_replace
            ), mock.patch.object(certifier.time, "sleep") as sleep:
                certifier.write_json_atomic(output, {"checkpoint": 1})

            self.assertEqual(json.loads(output.read_text(encoding="utf-8")), {
                "checkpoint": 1,
            })
            self.assertEqual(len(attempts), 2)
            sleep.assert_called_once_with(0.025)

    def test_atomic_checkpoint_replaces_read_only_onedrive_target(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            output = Path(temporary_directory) / "checkpoint.json"
            output.write_text('{"checkpoint": 1}\n', encoding="utf-8")
            output.chmod(stat.S_IREAD)

            certifier.write_json_atomic(output, {"checkpoint": 2})

            self.assertEqual(
                json.loads(output.read_text(encoding="utf-8")),
                {"checkpoint": 2},
            )

    def test_connector_budget_prefers_cell_coverage_gain(self):
        accepted = [
            self._accepted_edge(
                "edge-main", "main-a", "main-b", [0, 0, 10], [10, 0, 10]
            ),
            self._accepted_edge(
                "edge-local", "local-a", "local-b", [100, 0, 10], [110, 0, 10]
            ),
            self._accepted_edge(
                "edge-new-cell", "new-a", "new-b", [900, 0, 10], [910, 0, 10]
            ),
        ]
        plan = {
            "candidates": [candidate for candidate, _result in accepted],
            "node_owner": {
                "main-a": "central-r0-c0",
                "main-b": "central-r1-c0",
                "local-a": "central-r0-c0",
                "local-b": "central-r0-c0",
                "new-a": "central-r0-c1",
                "new-b": "central-r0-c1",
            },
        }
        work = {
            "results": {
                candidate["candidate_id"]: result
                for candidate, result in accepted
            }
        }
        policy = ({
            "max_distance_cm": 2000.0,
            "max_vertical_delta_cm": 200.0,
            "pairs_per_component_pair": 1,
            "max_candidates": 1,
        },)

        with tempfile.TemporaryDirectory() as temporary_directory, mock.patch.object(
            certifier, "GENERATED_CONNECTOR_ROUNDS", policy
        ):
            certifier.augment_plan_with_generated_connectors(
                plan,
                work,
                certifier.DEFAULT_SETTINGS,
                work_path=Path(temporary_directory) / "working.json",
            )

        proposals = work["generated_connector_candidates"]
        self.assertEqual(len(proposals), 1)
        self.assertIn("new-a", {
            proposals[0]["from_point_id"],
            proposals[0]["to_point_id"],
        })
        self.assertEqual(proposals[0]["tags"]["cell_coverage_gain"], 1)
        self.assertEqual(proposals[0]["tags"]["cell_union_count"], 3)

    def test_real_work_has_exact_nineteen_edge_semantic_cut(self):
        projected = certifier.read_json(certifier.PROJECTED_SOURCE_PATH)
        corrections = certifier.read_json(certifier.CORRECTIONS_PATH)
        plan = certifier.build_source_plan(projected, corrections)
        work = self._work_through_semantic_round(
            certifier.read_json(certifier.WORK_PATH), 0
        )
        plan["candidates"].extend(work.get("generated_connector_candidates", []))

        cut = certifier.minimum_semantic_recovery_cut(plan, work["results"])

        self.assertTrue(cut["feasible"])
        self.assertEqual(cut["rejected_edge_count"], 19)
        self.assertEqual(cut["unique_rejected_edge_count"], 19)
        self.assertAlmostEqual(cut["total_length_cm"], 25360.832, places=3)
        self.assertEqual(cut["reason_counts"], {
            "height_continuity": 9,
            "missing_support": 3,
            "multi_track": 2,
            "slope": 5,
        })
        self.assertIn(
            "candidate-osm-way-242113699-central-r0-c1-part-000-s000",
            cut["cut_candidate_ids"],
        )
        self.assertIn(
            "candidate-osm-way-963983555-central-r1-c1-part-000-s000",
            cut["cut_candidate_ids"],
        )

    def test_trusted_runtime_keeps_individually_certified_semantic_subsegments(self):
        candidates = []
        results = {}

        def add_candidate(
            candidate_id,
            origin,
            *,
            status="accepted",
            chain_id=None,
            source_id=None,
            segment_index=0,
            segment_count=1,
            offset=0.0,
        ):
            candidate = {
                "candidate_id": candidate_id,
                "topology_origin": origin,
                "from_point_id": candidate_id + "-a",
                "to_point_id": candidate_id + "-b",
            }
            if origin == "osm-semantic-recovery":
                candidate.update({
                    "semantic_chain_id": chain_id,
                    "semantic_source_candidate_id": source_id,
                    "semantic_segment_index": segment_index,
                    "semantic_segment_count": segment_count,
                    "semantic_start_point_id": chain_id + "-start",
                    "tags": {"semantic_lateral_offset_cm": offset},
                })
            candidates.append(candidate)
            results[candidate_id] = {
                "candidate_id": candidate_id,
                "status": status,
            }

        add_candidate("original", "openstreetmap")
        add_candidate("generated", "generated-connector")
        for index in range(2):
            add_candidate(
                "center-{}".format(index),
                "osm-semantic-recovery",
                chain_id="center-chain",
                source_id="rejected-source",
                segment_index=index,
                segment_count=2,
            )
            add_candidate(
                "offset-{}".format(index),
                "osm-semantic-recovery",
                chain_id="offset-chain",
                source_id="rejected-source",
                segment_index=index,
                segment_count=2,
                offset=20.0,
            )
            add_candidate(
                "partial-{}".format(index),
                "osm-semantic-recovery",
                status="accepted" if index == 0 else "rejected",
                chain_id="partial-chain",
                source_id="other-rejected-source",
                segment_index=index,
                segment_count=2,
            )

        selected = certifier.trusted_runtime_result_ids(
            {"candidates": candidates}, results
        )

        self.assertEqual(
            selected,
            {
                "original",
                "center-0",
                "center-1",
                "offset-0",
                "offset-1",
                "partial-0",
            },
        )
        self.assertNotIn("generated", selected)
        self.assertNotIn("partial-1", selected)

    def test_generated_connector_cannot_change_final_document_metrics(self):
        projected, source, corrections = certifier.make_synthetic_source()
        plan = certifier.build_source_plan(projected, corrections)
        settings = copy.deepcopy(certifier.DEFAULT_SETTINGS)
        results = {
            candidate["candidate_id"]: certifier.make_flat_result(
                candidate, settings
            )
            for candidate in plan["candidates"]
        }
        hashes = certifier.runtime_compatibility_hashes(settings)
        baseline, _baseline_audit = certifier.build_certified_document(
            plan, results, projected, source, hashes
        )

        generated = copy.deepcopy(plan["candidates"][0])
        generated["candidate_id"] = "generated-must-not-count"
        generated["source_feature_id"] = "generated-must-not-count"
        generated["topology_origin"] = "generated-connector"
        plan["candidates"].append(generated)
        results[generated["candidate_id"]] = certifier.make_flat_result(
            generated, settings
        )
        with_generated, _generated_audit = certifier.build_certified_document(
            plan, results, projected, source, hashes
        )

        self.assertEqual(
            with_generated["evidence"]["certified_lane_count"],
            baseline["evidence"]["certified_lane_count"],
        )
        self.assertEqual(
            with_generated["evidence"]["certified_directional_lane_length_cm"],
            baseline["evidence"]["certified_directional_lane_length_cm"],
        )

    def test_layer_seed_is_explicit_once_and_ground_requires_predecessor(self):
        class SeedRecordingAdapter(certifier.FakeProbeAdapter):
            def __init__(self):
                super().__init__()
                self.seed_queries = []

            def trace_support(self, x, y, expected_z, *, seed=False):
                if seed:
                    self.seed_queries.append((round(x, 4), round(y, 4)))
                return super().trace_support(x, y, expected_z, seed=seed)

        base = {
            "candidate_id": "semantic-layer-seed",
            "source_feature_id": "osm-way-layer-test",
            "source_cell_id": "central-r0-c0",
            "classification": "footway",
            "topology_origin": "osm-semantic-recovery",
            "from_point_id": "layer-a",
            "to_point_id": "layer-b",
            "from_source_position": [0.0, 0.0, 0.0],
            "to_source_position": [20.0, 0.0, 0.0],
            "source_length_xy_cm": 20.0,
            "tags": {"bridge": "yes", "highway": "footway", "layer": "2"},
            "requires_manual_review": False,
            "semantic_seed_policy": "explicit-layer-once",
        }
        adapter = SeedRecordingAdapter()
        accepted = certifier.exhaust_generator(
            certifier.certify_candidate_steps(
                base, adapter, certifier.DEFAULT_SETTINGS, {}
            )
        )
        self.assertEqual(accepted["status"], "accepted")
        self.assertTrue(adapter.seed_queries)
        self.assertEqual(len(set(adapter.seed_queries)), 1)

        ground = copy.deepcopy(base)
        ground["candidate_id"] = "semantic-ground-no-predecessor"
        ground["tags"] = {"footway": "sidewalk", "highway": "footway"}
        ground["classification"] = "sidewalk"
        ground["semantic_seed_policy"] = "certified-predecessor-required"
        rejected = certifier.exhaust_generator(
            certifier.certify_candidate_steps(
                ground, SeedRecordingAdapter(), certifier.DEFAULT_SETTINGS, {}
            )
        )
        self.assertEqual(rejected["status"], "rejected")
        self.assertEqual(rejected["reason"], "height_continuity")
        self.assertEqual(rejected["failure_evidence"]["phase"], "coarse-support")
        self.assertEqual(rejected["failure_evidence"]["station_distance_cm"], 0.0)

    def test_exhausted_direction_uses_finite_sidewalk_tiers_only(self):
        source = {
            "candidate_id": "ground-source",
            "source_feature_id": "ground-source",
            "source_cell_id": "central-r0-c0",
            "classification": "sidewalk",
            "topology_origin": "openstreetmap",
            "from_point_id": "ground-a",
            "to_point_id": "ground-b",
            "tags": {"highway": "footway", "footway": "sidewalk"},
        }
        plan = {"candidates": [source]}
        results = {
            source["candidate_id"]: {
                "candidate_id": source["candidate_id"],
                "status": "rejected",
                "reason": "slope",
            }
        }

        def add_failed_variant(offset):
            token = str(offset).replace("-", "m")
            candidate_id = "semantic-" + token
            candidate = {
                "candidate_id": candidate_id,
                "topology_origin": "osm-semantic-recovery",
                "semantic_chain_id": "chain-" + token,
                "semantic_source_candidate_id": source["candidate_id"],
                "semantic_segment_index": 0,
                "semantic_segment_count": 1,
                "semantic_start_point_id": source["from_point_id"],
                "tags": {"semantic_lateral_offset_cm": offset},
            }
            plan["candidates"].append(candidate)
            results[candidate_id] = {
                "candidate_id": candidate_id,
                "status": "rejected",
                "reason": "slope",
                "failure_evidence": {
                    "phase": "strict-longitudinal-continuity",
                    "station_distance_cm": 50.0,
                },
            }

        for offset in certifier.SEMANTIC_SIDEWALK_OFFSETS_CM:
            add_failed_variant(offset)
        state = certifier.semantic_recovery_attempt_state(plan, results)
        direction = (source["candidate_id"], source["from_point_id"])
        self.assertIn(direction, state["base_blocked_directions"])
        self.assertNotIn(direction, state["fully_blocked_directions"])
        self.assertEqual(
            certifier.semantic_offsets_for_next_attempt(
                source,
                source["from_point_id"],
                state,
                allow_expansion=False,
            ),
            (),
        )
        self.assertEqual(
            certifier.semantic_offsets_for_next_attempt(
                source,
                source["from_point_id"],
                state,
                allow_expansion=True,
            ),
            (50.0, -50.0),
        )

        missing_results = copy.deepcopy(results)
        missing_results["semantic-0.0"]["reason"] = "missing_support"
        missing_state = certifier.semantic_recovery_attempt_state(
            plan, missing_results
        )
        self.assertIn(direction, missing_state["fully_blocked_directions"])
        self.assertEqual(
            certifier.semantic_offsets_for_next_attempt(
                source,
                source["from_point_id"],
                missing_state,
                allow_expansion=True,
            ),
            (),
        )

        structured = copy.deepcopy(source)
        structured["candidate_id"] = "structured-source"
        structured["classification"] = "footway"
        structured["tags"] = {
            "highway": "steps",
            "bridge": "yes",
            "layer": "1",
        }
        structured_plan = {"candidates": [structured]}
        structured_results = {
            structured["candidate_id"]: {
                "candidate_id": structured["candidate_id"],
                "status": "rejected",
                "reason": "multi_track",
            }
        }
        structured_variant = {
            "candidate_id": "structured-semantic",
            "topology_origin": "osm-semantic-recovery",
            "semantic_chain_id": "structured-chain",
            "semantic_source_candidate_id": structured["candidate_id"],
            "semantic_segment_index": 0,
            "semantic_segment_count": 1,
            "semantic_start_point_id": structured["from_point_id"],
            "tags": {"semantic_lateral_offset_cm": 0.0},
        }
        structured_plan["candidates"].append(structured_variant)
        structured_results[structured_variant["candidate_id"]] = {
            "candidate_id": structured_variant["candidate_id"],
            "status": "rejected",
            "reason": "multi_track",
        }
        structured_state = certifier.semantic_recovery_attempt_state(
            structured_plan, structured_results
        )
        structured_direction = (
            structured["candidate_id"],
            structured["from_point_id"],
        )
        self.assertIn(
            structured_direction,
            structured_state["fully_blocked_directions"],
        )
        self.assertEqual(
            certifier.semantic_offsets_for_next_attempt(
                structured,
                structured["from_point_id"],
                structured_state,
                allow_expansion=True,
            ),
            (),
        )

    def test_semantic_round_uses_osm_segments_and_ground_only_offsets(self):
        projected = certifier.read_json(certifier.PROJECTED_SOURCE_PATH)
        corrections = certifier.read_json(certifier.CORRECTIONS_PATH)
        plan = certifier.build_source_plan(projected, corrections)
        work = self._work_through_semantic_round(
            certifier.read_json(certifier.WORK_PATH), 0
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_work = Path(temporary_directory) / "semantic-working.json"
            certifier.augment_plan_with_generated_connectors(
                plan,
                work,
                certifier.DEFAULT_SETTINGS,
                work_path=temporary_work,
            )
            certifier.augment_plan_with_semantic_recovery(
                plan,
                work,
                certifier.DEFAULT_SETTINGS,
                work_path=temporary_work,
            )

        proposals = work["semantic_recovery_candidates"]
        cut_ids = set(work["semantic_recovery_planner"]["cut_candidate_ids"])
        self.assertTrue(proposals)
        self.assertEqual(work["semantic_recovery_planner"]["rejected_edge_count"], 19)
        self.assertEqual(
            work["semantic_recovery_planner"]["planned_rejected_edge_count"], 78
        )
        self.assertEqual(
            work["semantic_recovery_planner"]["length_expansion_edge_count"], 59
        )
        self.assertGreaterEqual(
            work["semantic_recovery_planner"][
                "projected_trusted_directional_length_cm"
            ],
            300000.0,
        )
        self.assertTrue(
            {candidate["semantic_source_candidate_id"] for candidate in proposals}
            <= cut_ids
        )
        seeded_levels = []
        for candidate in proposals:
            self.assertEqual(candidate["topology_origin"], "osm-semantic-recovery")
            profile = certifier.semantic_profile(candidate)
            offset = float(candidate["tags"]["semantic_lateral_offset_cm"])
            if abs(offset) > 1.0e-6:
                self.assertTrue(profile["ground_sidewalk"])
            if candidate["semantic_seed_policy"] == "explicit-layer-once":
                self.assertTrue(profile["seed_eligible"])
                seeded_levels.append(profile["level_key"])
        self.assertEqual(len(seeded_levels), len(set(seeded_levels)))

    def test_live_round_four_is_minimal_ground_sidewalk_registration(self):
        projected = certifier.read_json(certifier.PROJECTED_SOURCE_PATH)
        corrections = certifier.read_json(certifier.CORRECTIONS_PATH)
        plan = certifier.build_source_plan(projected, corrections)
        work = self._work_through_semantic_round(
            certifier.read_json(certifier.WORK_PATH), 3
        )
        before = len(work["semantic_recovery_candidates"])
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_work = Path(temporary_directory) / "semantic-working.json"
            certifier.augment_plan_with_generated_connectors(
                plan,
                work,
                certifier.DEFAULT_SETTINGS,
                work_path=temporary_work,
            )
            certifier.augment_plan_with_semantic_recovery(
                plan,
                work,
                certifier.DEFAULT_SETTINGS,
                work_path=temporary_work,
            )

        proposals = work["semantic_recovery_candidates"][before:]
        self.assertEqual(len(proposals), 26)
        self.assertEqual(
            len({candidate["semantic_chain_id"] for candidate in proposals}), 8
        )
        self.assertEqual(
            {
                candidate["semantic_source_candidate_id"]
                for candidate in proposals
            },
            {
                "candidate-osm-way-1112861621-central-r0-c1-part-000-s000",
                "candidate-osm-way-963983553-central-r1-c1-part-000-s000",
                "candidate-osm-way-963983557-central-r0-c2-part-001-s000",
            },
        )
        self.assertEqual(
            {
                float(candidate["tags"]["semantic_lateral_offset_cm"])
                for candidate in proposals
            },
            {-50.0, 50.0},
        )
        self.assertTrue(
            all(certifier.semantic_profile(candidate)["ground_sidewalk"] for candidate in proposals)
        )
        planner = work["semantic_recovery_planner"]
        self.assertEqual(planner["recovery_mode"], "ground-sidewalk-registration")
        self.assertEqual(planner["registration_scope"], "connectivity-cut-only")
        self.assertFalse(planner["base_cut_feasible"])
        self.assertFalse(planner["expanded_cut_feasible"])


if __name__ == "__main__":
    unittest.main()
