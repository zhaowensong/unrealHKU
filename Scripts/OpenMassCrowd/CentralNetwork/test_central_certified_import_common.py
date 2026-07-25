#!/usr/bin/env python3
"""Host-side regression tests for the fail-closed certified asset importer."""

from __future__ import annotations

import copy
import hashlib
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
OPEN_MASS_DIR = SCRIPT_DIR.parent
for directory in (SCRIPT_DIR, OPEN_MASS_DIR):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

import central_certified_import_common as common
import verify_central_network_cache as verifier


class CertifiedImportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = common.load_json_strict(
            SCRIPT_DIR / "central_network_certified.schema.json"
        )

    def valid_document(self):
        document = verifier.build_self_test_fixture()
        document["source_provenance"] = {
            "dataset_id": "fixture-source",
            "source_path": "fixture-source.json",
            "topology_sha256": document["hashes"]["topology_sha256"],
            "document_sha256": hashlib.sha256(
                b"fixture-source-document"
            ).hexdigest(),
        }
        return document

    def test_valid_certified_fixture_passes(self):
        metrics = common.validate_certified_document(
            self.valid_document(), self.schema
        )
        self.assertTrue(metrics["schema_valid"])
        self.assertTrue(metrics["hashes_valid"])
        self.assertTrue(metrics["certified_only"])
        self.assertEqual(metrics["cell_count"], 6)
        self.assertEqual(metrics["target_population"], 300)

    def test_candidate_source_cannot_pass_certified_schema(self):
        source = common.load_json_strict(
            SCRIPT_DIR / "Data" / "central_pedestrian_source.json"
        )
        with self.assertRaises(common.CertifiedImportError):
            common.validate_certified_document(source, self.schema)

    def test_uncertified_cell_is_rejected(self):
        document = self.valid_document()
        document["cells"][0]["certified"] = False
        with self.assertRaises(common.CertifiedImportError):
            common.validate_certified_document(document, self.schema)

    def test_payload_tampering_breaks_hash_gate(self):
        document = self.valid_document()
        document["cells"][0]["nodes"][0]["position"][0] += 1.0
        with self.assertRaisesRegex(
            common.CertifiedImportError, "cell content hash mismatch"
        ):
            common.validate_certified_document(document, self.schema)

    def test_duplicate_json_keys_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "duplicate.json"
            path.write_text(
                '{"schema_version":1,"schema_version":1}', encoding="utf-8"
            )
            with self.assertRaisesRegex(
                common.CertifiedImportError, "duplicate JSON key"
            ):
                common.load_json_strict(path)


if __name__ == "__main__":
    unittest.main(verbosity=2)
