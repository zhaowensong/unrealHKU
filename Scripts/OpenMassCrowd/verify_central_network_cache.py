#!/usr/bin/env python3
"""Strictly verify the certified Central pedestrian-network audit mirror.

This verifier consumes only ``CentralNetwork/Data/central_network_certified.json``.
The OSM source and Unreal-projected candidate mirrors are provenance inputs and
can never satisfy this verifier by themselves.

Hash contract (schema version 1)
--------------------------------
JSON is canonicalized with sorted keys, UTF-8, no ASCII escaping and compact
separators. A cell's ``cell_content_sha256`` hashes the complete cell object
with its ``hashes`` member removed. Its ``combined_sha256`` hashes an object
containing the four compatibility hashes plus ``cell_content_sha256``. The
root ``cell_content_sha256`` hashes the ordered cell-id/content/combined digest
list, and the root ``combined_sha256`` uses the same compatibility-hash object
with that aggregate content digest. This makes payload tampering observable,
not merely hash-shaped.

Exit status is zero only when every required check passes. A stable and a
timestamped machine-readable report are written under ``Saved/Reports`` unless
``--report`` selects a single output path.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import sys
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

try:
    from jsonschema import Draft202012Validator, FormatChecker
except ImportError as error:  # pragma: no cover - exercised on misconfigured hosts
    raise SystemExit(
        "jsonschema is required; install Scripts/OpenMassCrowd/CentralNetwork/requirements.txt"
    ) from error


SCHEMA_VERSION = 2
VERIFIER_VERSION = "1.3.0"
MINIMUM_CELLS = 6
MINIMUM_GEOGRAPHIC_BLOCKS = 4
MINIMUM_CONNECTED_STREET_BLOCKS = 4
MINIMUM_JUNCTIONS = 8
MINIMUM_DIRECTIONAL_LANE_LENGTH_CM = 300_000.0
MINIMUM_SPAWN_DISTRICTS = 6
TARGET_POPULATION = 300
FULL_GROUND_EVIDENCE_MASK = 63
POSITION_TOLERANCE_CM = 5.0
LENGTH_TOLERANCE_CM = 0.5
MINIMUM_WALKABLE_SURFACE_NORMAL_Z = 0.5
MAXIMUM_SURFACE_SLOPE_DEGREES = math.degrees(
    math.acos(MINIMUM_WALKABLE_SURFACE_NORMAL_Z)
)
MAXIMUM_SAMPLE_HEIGHT_DELTA_CM = 55.0
MAXIMUM_SAMPLE_GRADE = 0.96
MAXIMUM_SUPPORT_SPACING_CM = 10.0
SUPPORT_SPACING_TOLERANCE_CM = 1.0

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parents[1]
CENTRAL_DIR = SCRIPT_DIR / "CentralNetwork"
DEFAULT_CACHE_PATH = CENTRAL_DIR / "Data" / "central_network_certified.json"
DEFAULT_SCHEMA_PATH = CENTRAL_DIR / "central_network_certified.schema.json"
DEFAULT_SOURCE_SCHEMA_PATH = CENTRAL_DIR / "central_pedestrian_source.schema.json"
DEFAULT_REPORT_DIR = PROJECT_ROOT / "Saved" / "Reports"

CHECK_NAMES = (
    "schema",
    "hashes",
    "certified_only",
    "node_references",
    "reverse_lanes",
    "portals",
    "connectivity",
    "coverage",
    "spawn_districts",
    "population",
    "evidence",
    "source_provenance",
)

HASH_INPUT_KEYS = (
    "topology_sha256",
    "georeference_sha256",
    "tileset_sha256",
    "collision_settings_sha256",
)


class DuplicateKeyError(ValueError):
    """Raised before validation so duplicate JSON fields cannot hide data."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def reject_duplicate_keys(pairs: Iterable[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateKeyError(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8-sig") as stream:
        return json.load(stream, object_pairs_hook=reject_duplicate_keys)


def json_path(parts: Iterable[Any]) -> str:
    result = "$"
    for part in parts:
        if isinstance(part, int):
            result += f"[{part}]"
        else:
            result += "." + str(part)
    return result


def vector_distance(first: list[float], second: list[float]) -> float:
    return math.sqrt(sum((float(a) - float(b)) ** 2 for a, b in zip(first, second)))


def vector_distance_2d(first: list[float], second: list[float]) -> float:
    return math.hypot(float(first[0]) - float(second[0]), float(first[1]) - float(second[1]))


def point_in_bounds(point: list[float], bounds: dict[str, list[float]], tolerance: float = 0.0) -> bool:
    return all(
        float(bounds["min"][axis]) - tolerance
        <= float(point[axis])
        <= float(bounds["max"][axis]) + tolerance
        for axis in range(3)
    )


def expected_combined_hash(hashes: dict[str, str], content_sha256: str) -> str:
    payload = {key: hashes[key] for key in HASH_INPUT_KEYS}
    payload["cell_content_sha256"] = content_sha256
    return sha256_json(payload)


def expected_cell_hashes(cell: dict[str, Any], root_hashes: dict[str, str]) -> tuple[str, str]:
    payload = copy.deepcopy(cell)
    payload.pop("hashes", None)
    content_hash = sha256_json(payload)
    return content_hash, expected_combined_hash(root_hashes, content_hash)


def expected_root_hashes(document: dict[str, Any]) -> tuple[str, str]:
    digest_rows = []
    for cell in sorted(document["cells"], key=lambda item: item["cell_id"]):
        content_hash, combined_hash = expected_cell_hashes(cell, document["hashes"])
        digest_rows.append(
            {
                "cell_id": cell["cell_id"],
                "cell_content_sha256": content_hash,
                "combined_sha256": combined_hash,
            }
        )
    content_hash = sha256_json(digest_rows)
    return content_hash, expected_combined_hash(document["hashes"], content_hash)


class Verification:
    def __init__(self, input_path: Path | None):
        self.input_path = input_path
        self.checks: dict[str, dict[str, Any]] = {
            name: {"executed": False, "passed": None, "metrics": {}, "issue_count": 0}
            for name in CHECK_NAMES
        }
        self.issues: list[dict[str, Any]] = []

    def execute(self, check: str) -> None:
        self.checks[check]["executed"] = True

    def metric(self, check: str, name: str, value: Any) -> None:
        self.execute(check)
        self.checks[check]["metrics"][name] = value

    def fail(
        self,
        check: str,
        code: str,
        path: str,
        message: str,
        *,
        expected: Any = None,
        actual: Any = None,
    ) -> None:
        self.execute(check)
        issue = {"check": check, "code": code, "path": path, "message": message}
        if expected is not None:
            issue["expected"] = expected
        if actual is not None:
            issue["actual"] = actual
        self.issues.append(issue)
        self.checks[check]["issue_count"] += 1

    def finalize(self) -> None:
        for check in self.checks.values():
            if check["executed"]:
                check["passed"] = check["issue_count"] == 0

    @property
    def overall_passed(self) -> bool:
        return all(check["passed"] is True for check in self.checks.values())


def validate_against_schema(
    verification: Verification,
    document: Any,
    schema: dict[str, Any],
) -> bool:
    verification.execute("schema")
    try:
        Draft202012Validator.check_schema(schema)
    except Exception as error:
        verification.fail("schema", "invalid-verifier-schema", "$schema", str(error))
        return False

    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = sorted(validator.iter_errors(document), key=lambda error: list(error.absolute_path))
    for error in errors:
        verification.fail(
            "schema",
            "schema-violation",
            json_path(error.absolute_path),
            error.message,
        )
    verification.metric("schema", "violation_count", len(errors))
    return not errors


def verify_hashes(verification: Verification, document: dict[str, Any]) -> None:
    verification.execute("hashes")
    root_hashes = document["hashes"]
    expected_root_content, expected_root_combined = expected_root_hashes(document)

    for cell_index, cell in enumerate(document["cells"]):
        cell_path = f"$.cells[{cell_index}].hashes"
        cell_hashes = cell["hashes"]
        for key in HASH_INPUT_KEYS:
            if cell_hashes[key] != root_hashes[key]:
                verification.fail(
                    "hashes",
                    "cell-compatibility-hash-mismatch",
                    f"{cell_path}.{key}",
                    "cell compatibility hash must exactly match the network hash",
                    expected=root_hashes[key],
                    actual=cell_hashes[key],
                )

        expected_content, expected_combined = expected_cell_hashes(cell, root_hashes)
        if cell_hashes["cell_content_sha256"] != expected_content:
            verification.fail(
                "hashes",
                "cell-content-hash-mismatch",
                f"{cell_path}.cell_content_sha256",
                "cell payload does not match its canonical content hash",
                expected=expected_content,
                actual=cell_hashes["cell_content_sha256"],
            )
        if cell_hashes["combined_sha256"] != expected_combined:
            verification.fail(
                "hashes",
                "cell-combined-hash-mismatch",
                f"{cell_path}.combined_sha256",
                "cell compatibility/content aggregate hash is stale",
                expected=expected_combined,
                actual=cell_hashes["combined_sha256"],
            )

    if root_hashes["cell_content_sha256"] != expected_root_content:
        verification.fail(
            "hashes",
            "root-content-hash-mismatch",
            "$.hashes.cell_content_sha256",
            "ordered certified-cell digest list does not match the root content hash",
            expected=expected_root_content,
            actual=root_hashes["cell_content_sha256"],
        )
    if root_hashes["combined_sha256"] != expected_root_combined:
        verification.fail(
            "hashes",
            "root-combined-hash-mismatch",
            "$.hashes.combined_sha256",
            "root compatibility/content aggregate hash is stale",
            expected=expected_root_combined,
            actual=root_hashes["combined_sha256"],
        )

    verification.metric("hashes", "cell_hashes_verified", len(document["cells"]))
    verification.metric("hashes", "canonicalization", "sorted-compact-utf8-json-v1")


def strongly_connected_components(nodes: set[str], outgoing: dict[str, set[str]]) -> list[set[str]]:
    index = 0
    stack: list[str] = []
    on_stack: set[str] = set()
    indices: dict[str, int] = {}
    low_links: dict[str, int] = {}
    components: list[set[str]] = []

    def visit(node: str) -> None:
        nonlocal index
        indices[node] = index
        low_links[node] = index
        index += 1
        stack.append(node)
        on_stack.add(node)

        for neighbor in outgoing.get(node, set()):
            if neighbor not in indices:
                visit(neighbor)
                low_links[node] = min(low_links[node], low_links[neighbor])
            elif neighbor in on_stack:
                low_links[node] = min(low_links[node], indices[neighbor])

        if low_links[node] == indices[node]:
            component: set[str] = set()
            while True:
                member = stack.pop()
                on_stack.remove(member)
                component.add(member)
                if member == node:
                    break
            components.append(component)

    for node in sorted(nodes):
        if node not in indices:
            visit(node)
    return components


def weak_components(nodes: set[str], adjacency: dict[str, set[str]]) -> list[set[str]]:
    remaining = set(nodes)
    components: list[set[str]] = []
    while remaining:
        start = min(remaining)
        queue = deque([start])
        component = {start}
        remaining.remove(start)
        while queue:
            node = queue.popleft()
            for neighbor in adjacency.get(node, set()):
                if neighbor in remaining:
                    remaining.remove(neighbor)
                    component.add(neighbor)
                    queue.append(neighbor)
        components.append(component)
    return components


def verify_network(
    verification: Verification,
    document: dict[str, Any],
    *,
    minimum_directional_lane_length_cm: float = MINIMUM_DIRECTIONAL_LANE_LENGTH_CM,
) -> dict[str, Any]:
    for check in (
        "certified_only",
        "node_references",
        "reverse_lanes",
        "portals",
        "connectivity",
        "coverage",
        "spawn_districts",
        "population",
        "evidence",
    ):
        verification.execute(check)

    cells_by_id: dict[str, dict[str, Any]] = {}
    nodes_by_id: dict[str, dict[str, Any]] = {}
    lanes_by_id: dict[str, dict[str, Any]] = {}
    portals_by_id: dict[str, dict[str, Any]] = {}
    node_owner: dict[str, str] = {}
    lane_owner: dict[str, str] = {}
    portal_owner: dict[str, str] = {}

    def add_unique(
        collection: dict[str, dict[str, Any]],
        identifier: str,
        value: dict[str, Any],
        check: str,
        path: str,
        kind: str,
    ) -> None:
        if identifier in collection:
            verification.fail(
                check,
                f"duplicate-{kind}-id",
                path,
                f"{kind} identifiers must be globally unique",
                actual=identifier,
            )
        else:
            collection[identifier] = value

    for cell_index, cell in enumerate(document["cells"]):
        cell_path = f"$.cells[{cell_index}]"
        cell_id = cell["cell_id"]
        add_unique(cells_by_id, cell_id, cell, "node_references", f"{cell_path}.cell_id", "cell")
        if not cell["certified"]:
            verification.fail(
                "certified_only",
                "uncertified-cell",
                f"{cell_path}.certified",
                "runtime cache may contain certified cells only",
            )

        bounds = cell["world_bounds"]
        if any(float(bounds["min"][axis]) > float(bounds["max"][axis]) for axis in range(3)):
            verification.fail(
                "node_references",
                "invalid-cell-bounds",
                f"{cell_path}.world_bounds",
                "every bounds minimum must be less than or equal to its maximum",
            )

        for node_index, node in enumerate(cell["nodes"]):
            node_path = f"{cell_path}.nodes[{node_index}]"
            node_id = node["node_id"]
            add_unique(nodes_by_id, node_id, node, "node_references", f"{node_path}.node_id", "node")
            node_owner.setdefault(node_id, cell_id)
            if node["cell_id"] != cell_id:
                verification.fail(
                    "node_references",
                    "node-owner-mismatch",
                    f"{node_path}.cell_id",
                    "node cell_id must match its owning cell",
                    expected=cell_id,
                    actual=node["cell_id"],
                )
            if not point_in_bounds(node["position"], bounds, POSITION_TOLERANCE_CM):
                verification.fail(
                    "node_references",
                    "node-outside-cell-bounds",
                    f"{node_path}.position",
                    "node position is outside its owning cell bounds",
                    actual=node["position"],
                )

        source_feature_ids = set(cell["source_feature_ids"])
        for lane_index, lane in enumerate(cell["directed_lanes"]):
            lane_path = f"{cell_path}.directed_lanes[{lane_index}]"
            lane_id = lane["lane_id"]
            add_unique(lanes_by_id, lane_id, lane, "node_references", f"{lane_path}.lane_id", "lane")
            lane_owner.setdefault(lane_id, cell_id)
            if lane["cell_id"] != cell_id:
                verification.fail(
                    "node_references",
                    "lane-owner-mismatch",
                    f"{lane_path}.cell_id",
                    "lane cell_id must match its owning cell",
                    expected=cell_id,
                    actual=lane["cell_id"],
                )
            if lane["source_feature_id"] not in source_feature_ids:
                verification.fail(
                    "node_references",
                    "lane-source-feature-not-in-cell",
                    f"{lane_path}.source_feature_id",
                    "lane source feature must be recorded by its owning cell",
                    actual=lane["source_feature_id"],
                )
            if not lane["certified"]:
                verification.fail(
                    "certified_only",
                    "uncertified-lane",
                    f"{lane_path}.certified",
                    "runtime cache may contain certified lanes only",
                )

            samples = lane["ground_samples"]
            sample_ids = [sample["sample_id"] for sample in samples]
            if len(set(sample_ids)) != len(sample_ids):
                verification.fail(
                    "certified_only",
                    "duplicate-ground-sample-id",
                    f"{lane_path}.ground_samples",
                    "ground sample identifiers must be unique within a lane",
                )
            indices = [sample["sample_index"] for sample in samples]
            if indices != list(range(len(samples))):
                verification.fail(
                    "certified_only",
                    "noncanonical-ground-sample-indices",
                    f"{lane_path}.ground_samples",
                    "ground sample indices must be contiguous and start at zero",
                    expected=list(range(len(samples))),
                    actual=indices,
                )
            distances = [float(sample["distance_along_lane_cm"]) for sample in samples]
            if abs(distances[0]) > LENGTH_TOLERANCE_CM:
                verification.fail(
                    "certified_only",
                    "lane-samples-do-not-start-at-zero",
                    f"{lane_path}.ground_samples[0].distance_along_lane_cm",
                    "first sample must be at lane distance zero",
                    actual=distances[0],
                )
            if any(second <= first for first, second in zip(distances, distances[1:])):
                verification.fail(
                    "certified_only",
                    "nonincreasing-ground-sample-distance",
                    f"{lane_path}.ground_samples",
                    "sample distances must increase strictly along a directed lane",
                )
            if abs(distances[-1] - float(lane["length_cm"])) > LENGTH_TOLERANCE_CM:
                verification.fail(
                    "certified_only",
                    "lane-samples-do-not-end-at-length",
                    f"{lane_path}.ground_samples[-1].distance_along_lane_cm",
                    "last sample distance must equal lane length",
                    expected=lane["length_cm"],
                    actual=distances[-1],
                )
            geometry_length_cm = 0.0
            for sample_index, sample in enumerate(samples):
                sample_path = f"{lane_path}.ground_samples[{sample_index}]"
                if sample["evidence_mask"] != FULL_GROUND_EVIDENCE_MASK:
                    verification.fail(
                        "certified_only",
                        "incomplete-ground-evidence",
                        f"{sample_path}.evidence_mask",
                        "every admitted sample must contain all six certification evidence bits",
                        expected=FULL_GROUND_EVIDENCE_MASK,
                        actual=sample["evidence_mask"],
                    )
                normal_length = math.sqrt(sum(float(value) ** 2 for value in sample["surface_normal"]))
                normal_z = float(sample["surface_normal"][2])
                if (
                    abs(normal_length - 1.0) > 0.05
                    or normal_z < MINIMUM_WALKABLE_SURFACE_NORMAL_Z
                ):
                    verification.fail(
                        "certified_only",
                        "invalid-surface-normal",
                        f"{sample_path}.surface_normal",
                        "stored surface normal must be normalized and satisfy the certified raw-normal guard",
                        expected={"length": "1.0 +/- 0.05", "minimum_z": MINIMUM_WALKABLE_SURFACE_NORMAL_Z},
                        actual=sample["surface_normal"],
                    )
                slope_degrees = float(sample["surface_slope_degrees"])
                if slope_degrees < 0.0 or slope_degrees > MAXIMUM_SURFACE_SLOPE_DEGREES + 0.1:
                    verification.fail(
                        "certified_only",
                        "invalid-surface-slope",
                        f"{sample_path}.surface_slope_degrees",
                        "stored raw-normal slope must satisfy the same guard as the runtime cache",
                        expected=f"0..{MAXIMUM_SURFACE_SLOPE_DEGREES + 0.1:.4f}",
                        actual=slope_degrees,
                    )
                neighbor_delta_cm = float(sample["max_neighbor_height_delta_cm"])
                if neighbor_delta_cm < 0.0 or neighbor_delta_cm > MAXIMUM_SAMPLE_HEIGHT_DELTA_CM:
                    verification.fail(
                        "certified_only",
                        "invalid-neighbor-height-delta",
                        f"{sample_path}.max_neighbor_height_delta_cm",
                        "stored neighbor height delta exceeds the runtime pedestrian continuity limit",
                        expected=f"0..{MAXIMUM_SAMPLE_HEIGHT_DELTA_CM}",
                        actual=neighbor_delta_cm,
                    )
                center = sample["center_position"]
                if (
                    vector_distance_2d(center, sample["left_track_position"]) <= 1.0
                    or vector_distance_2d(center, sample["right_track_position"]) <= 1.0
                ):
                    verification.fail(
                        "certified_only",
                        "degenerate-support-track",
                        sample_path,
                        "left and right support tracks must be distinct from the center track",
                    )
                if sample_index > 0:
                    previous = samples[sample_index - 1]["center_position"]
                    horizontal_cm = vector_distance_2d(previous, center)
                    height_delta_cm = abs(float(center[2]) - float(previous[2]))
                    grade = (
                        height_delta_cm / horizontal_cm
                        if horizontal_cm > 1.0e-6
                        else math.inf
                    )
                    segment_length_cm = vector_distance(previous, center)
                    geometry_length_cm += segment_length_cm
                    if (
                        horizontal_cm <= 1.0e-6
                        or horizontal_cm
                        > MAXIMUM_SUPPORT_SPACING_CM + SUPPORT_SPACING_TOLERANCE_CM
                        or height_delta_cm > MAXIMUM_SAMPLE_HEIGHT_DELTA_CM
                        or grade > MAXIMUM_SAMPLE_GRADE
                    ):
                        verification.fail(
                            "certified_only",
                            "invalid-ground-segment-continuity",
                            sample_path,
                            "adjacent exact-XY samples must satisfy runtime spacing, step, and grade limits",
                            expected={
                                "maximum_horizontal_cm": MAXIMUM_SUPPORT_SPACING_CM + SUPPORT_SPACING_TOLERANCE_CM,
                                "maximum_height_delta_cm": MAXIMUM_SAMPLE_HEIGHT_DELTA_CM,
                                "maximum_grade": MAXIMUM_SAMPLE_GRADE,
                            },
                            actual={
                                "horizontal_cm": horizontal_cm,
                                "height_delta_cm": height_delta_cm,
                                "grade": grade,
                            },
                        )
            geometry_tolerance_cm = max(2.0, float(lane["length_cm"]) * 0.01)
            if abs(geometry_length_cm - float(lane["length_cm"])) > geometry_tolerance_cm:
                verification.fail(
                    "certified_only",
                    "lane-sample-geometry-length-mismatch",
                    f"{lane_path}.ground_samples",
                    "sample polyline length must match the declared runtime lane length",
                    expected=lane["length_cm"],
                    actual=geometry_length_cm,
                )

        for portal_index, portal in enumerate(cell["portals"]):
            portal_path = f"{cell_path}.portals[{portal_index}]"
            portal_id = portal["portal_id"]
            add_unique(portals_by_id, portal_id, portal, "portals", f"{portal_path}.portal_id", "portal")
            portal_owner.setdefault(portal_id, cell_id)
            if not portal["certified"]:
                verification.fail(
                    "certified_only",
                    "uncertified-portal",
                    f"{portal_path}.certified",
                    "runtime cache may contain certified portals only",
                )

    derived_incoming: dict[str, set[str]] = defaultdict(set)
    derived_outgoing: dict[str, set[str]] = defaultdict(set)
    directed_adjacency: dict[str, set[str]] = defaultdict(set)
    weak_adjacency: dict[str, set[str]] = defaultdict(set)
    cross_cell_lane_ids: set[str] = set()
    total_lane_length_cm = 0.0
    total_ground_samples = 0

    for lane_id, lane in lanes_by_id.items():
        lane_path = f"lane:{lane_id}"
        from_id = lane["from_node_id"]
        to_id = lane["to_node_id"]
        from_node = nodes_by_id.get(from_id)
        to_node = nodes_by_id.get(to_id)
        if from_node is None:
            verification.fail(
                "node_references",
                "missing-from-node",
                f"{lane_path}.from_node_id",
                "lane references a missing start node",
                actual=from_id,
            )
        if to_node is None:
            verification.fail(
                "node_references",
                "missing-to-node",
                f"{lane_path}.to_node_id",
                "lane references a missing destination node",
                actual=to_id,
            )
        if from_node is None or to_node is None:
            continue
        if node_owner[from_id] != lane_owner[lane_id]:
            verification.fail(
                "node_references",
                "lane-start-not-in-owner-cell",
                f"{lane_path}.from_node_id",
                "a directed lane must be owned by its start-node cell",
                expected=lane_owner[lane_id],
                actual=node_owner[from_id],
            )
        if (
            lane["component_id"] != from_node["component_id"]
            or lane["component_id"] != to_node["component_id"]
        ):
            verification.fail(
                "connectivity",
                "lane-crosses-component-gap",
                lane_path,
                "a certified lane and both endpoints must share one component_id",
                actual={
                    "lane": lane["component_id"],
                    "from": from_node["component_id"],
                    "to": to_node["component_id"],
                },
            )

        derived_outgoing[from_id].add(lane_id)
        derived_incoming[to_id].add(lane_id)
        directed_adjacency[from_id].add(to_id)
        weak_adjacency[from_id].add(to_id)
        weak_adjacency[to_id].add(from_id)
        total_lane_length_cm += float(lane["length_cm"])
        total_ground_samples += len(lane["ground_samples"])

        if node_owner[from_id] != node_owner[to_id]:
            cross_cell_lane_ids.add(lane_id)

        samples = lane["ground_samples"]
        if vector_distance(samples[0]["center_position"], from_node["position"]) > POSITION_TOLERANCE_CM:
            verification.fail(
                "node_references",
                "lane-start-sample-node-mismatch",
                f"{lane_path}.ground_samples[0].center_position",
                "first ground sample must coincide with the start node",
            )
        if vector_distance(samples[-1]["center_position"], to_node["position"]) > POSITION_TOLERANCE_CM:
            verification.fail(
                "node_references",
                "lane-end-sample-node-mismatch",
                f"{lane_path}.ground_samples[-1].center_position",
                "last ground sample must coincide with the destination node",
            )
        straight_distance = vector_distance(from_node["position"], to_node["position"])
        if float(lane["length_cm"]) + LENGTH_TOLERANCE_CM < straight_distance:
            verification.fail(
                "node_references",
                "lane-shorter-than-endpoint-distance",
                f"{lane_path}.length_cm",
                "lane length cannot be shorter than its endpoint chord",
                expected=f">={straight_distance}",
                actual=lane["length_cm"],
            )

    for node_id, node in nodes_by_id.items():
        expected_incoming = derived_incoming.get(node_id, set())
        expected_outgoing = derived_outgoing.get(node_id, set())
        if set(node["incoming_lane_ids"]) != expected_incoming:
            verification.fail(
                "node_references",
                "incoming-lane-index-mismatch",
                f"node:{node_id}.incoming_lane_ids",
                "stored incoming-lane index must exactly match lane endpoints",
                expected=sorted(expected_incoming),
                actual=sorted(node["incoming_lane_ids"]),
            )
        if set(node["outgoing_lane_ids"]) != expected_outgoing:
            verification.fail(
                "node_references",
                "outgoing-lane-index-mismatch",
                f"node:{node_id}.outgoing_lane_ids",
                "stored outgoing-lane index must exactly match lane endpoints",
                expected=sorted(expected_outgoing),
                actual=sorted(node["outgoing_lane_ids"]),
            )
        if not expected_incoming or not expected_outgoing:
            verification.fail(
                "connectivity",
                "node-not-routable-both-directions",
                f"node:{node_id}",
                "every promoted node must have at least one incoming and outgoing certified lane",
            )

    for lane_id, lane in lanes_by_id.items():
        reverse_id = lane["reverse_lane_id"]
        reverse = lanes_by_id.get(reverse_id)
        if reverse is None:
            verification.fail(
                "reverse_lanes",
                "missing-reverse-lane",
                f"lane:{lane_id}.reverse_lane_id",
                "every promoted directional lane must name its reverse lane",
                actual=reverse_id,
            )
            continue
        if reverse_id == lane_id:
            verification.fail(
                "reverse_lanes",
                "self-reverse-lane",
                f"lane:{lane_id}.reverse_lane_id",
                "a lane cannot be its own reverse",
            )
        if reverse["reverse_lane_id"] != lane_id:
            verification.fail(
                "reverse_lanes",
                "asymmetric-reverse-lane-reference",
                f"lane:{reverse_id}.reverse_lane_id",
                "reverse relationship must be symmetric",
                expected=lane_id,
                actual=reverse["reverse_lane_id"],
            )
        if reverse["from_node_id"] != lane["to_node_id"] or reverse["to_node_id"] != lane["from_node_id"]:
            verification.fail(
                "reverse_lanes",
                "reverse-lane-endpoint-mismatch",
                f"lane:{reverse_id}",
                "reverse lane endpoints must be exactly swapped",
            )
        if reverse["component_id"] != lane["component_id"]:
            verification.fail(
                "reverse_lanes",
                "reverse-lane-component-mismatch",
                f"lane:{reverse_id}.component_id",
                "reverse lane must remain inside the same certified component",
            )
        if reverse["pedestrian_class"] != lane["pedestrian_class"]:
            verification.fail(
                "reverse_lanes",
                "reverse-lane-class-mismatch",
                f"lane:{reverse_id}.pedestrian_class",
                "reverse lane must preserve pedestrian classification",
            )
        if abs(float(reverse["length_cm"]) - float(lane["length_cm"])) > LENGTH_TOLERANCE_CM:
            verification.fail(
                "reverse_lanes",
                "reverse-lane-length-mismatch",
                f"lane:{reverse_id}.length_cm",
                "reverse lane length must match its pair",
                expected=lane["length_cm"],
                actual=reverse["length_cm"],
            )

    portal_lane_ids: set[str] = set()
    for portal_id, portal in portals_by_id.items():
        owner_cell = portal_owner[portal_id]
        local_cell = portal["local_cell_id"]
        remote_cell = portal["remote_cell_id"]
        local_node = nodes_by_id.get(portal["local_node_id"])
        remote_node = nodes_by_id.get(portal["remote_node_id"])
        lane = lanes_by_id.get(portal["directed_lane_id"])
        reverse = portals_by_id.get(portal["reverse_portal_id"])

        if local_cell != owner_cell:
            verification.fail(
                "portals",
                "portal-owner-mismatch",
                f"portal:{portal_id}.local_cell_id",
                "portal local cell must match its owning cell",
                expected=owner_cell,
                actual=local_cell,
            )
        if local_cell not in cells_by_id or remote_cell not in cells_by_id or local_cell == remote_cell:
            verification.fail(
                "portals",
                "invalid-portal-cell-reference",
                f"portal:{portal_id}",
                "portal must connect two distinct existing cells",
            )
        if local_node is None or remote_node is None:
            verification.fail(
                "portals",
                "missing-portal-node",
                f"portal:{portal_id}",
                "portal references a missing node",
            )
        else:
            if node_owner[portal["local_node_id"]] != local_cell:
                verification.fail(
                    "portals",
                    "portal-local-node-cell-mismatch",
                    f"portal:{portal_id}.local_node_id",
                    "portal local node must belong to local cell",
                )
            if node_owner[portal["remote_node_id"]] != remote_cell:
                verification.fail(
                    "portals",
                    "portal-remote-node-cell-mismatch",
                    f"portal:{portal_id}.remote_node_id",
                    "portal remote node must belong to remote cell",
                )
            if vector_distance(portal["position"], local_node["position"]) > POSITION_TOLERANCE_CM:
                verification.fail(
                    "portals",
                    "portal-position-node-mismatch",
                    f"portal:{portal_id}.position",
                    "directed portal position must coincide with its local node",
                )
        if lane is None:
            verification.fail(
                "portals",
                "missing-portal-lane",
                f"portal:{portal_id}.directed_lane_id",
                "portal references a missing directed lane",
            )
        else:
            portal_lane_ids.add(portal["directed_lane_id"])
            if lane_owner[portal["directed_lane_id"]] != local_cell:
                verification.fail(
                    "portals",
                    "portal-lane-owner-mismatch",
                    f"portal:{portal_id}.directed_lane_id",
                    "portal lane must be owned by the local cell",
                )
            if lane["from_node_id"] != portal["local_node_id"] or lane["to_node_id"] != portal["remote_node_id"]:
                verification.fail(
                    "portals",
                    "portal-lane-endpoint-mismatch",
                    f"portal:{portal_id}.directed_lane_id",
                    "portal lane endpoints must match its local and remote nodes",
                )
        if reverse is None:
            verification.fail(
                "portals",
                "missing-reverse-portal",
                f"portal:{portal_id}.reverse_portal_id",
                "every promoted portal must name its reverse portal",
                actual=portal["reverse_portal_id"],
            )
        else:
            if reverse["reverse_portal_id"] != portal_id:
                verification.fail(
                    "portals",
                    "asymmetric-reverse-portal-reference",
                    f"portal:{portal['reverse_portal_id']}.reverse_portal_id",
                    "reverse portal relationship must be symmetric",
                )
            if (
                reverse["local_cell_id"] != remote_cell
                or reverse["remote_cell_id"] != local_cell
                or reverse["local_node_id"] != portal["remote_node_id"]
                or reverse["remote_node_id"] != portal["local_node_id"]
            ):
                verification.fail(
                    "portals",
                    "reverse-portal-endpoint-mismatch",
                    f"portal:{portal['reverse_portal_id']}",
                    "reverse portal must swap both cells and nodes",
                )
            if lane is not None and reverse["directed_lane_id"] != lane["reverse_lane_id"]:
                verification.fail(
                    "portals",
                    "reverse-portal-lane-mismatch",
                    f"portal:{portal['reverse_portal_id']}.directed_lane_id",
                    "reverse portal must use the reverse directional lane",
                )

    if portal_lane_ids != cross_cell_lane_ids:
        verification.fail(
            "portals",
            "cross-cell-lane-portal-coverage-mismatch",
            "$.cells[*].portals",
            "cross-cell lanes and portal lane references must form the same set",
            expected=sorted(cross_cell_lane_ids),
            actual=sorted(portal_lane_ids),
        )

    node_ids = set(nodes_by_id)
    weak = weak_components(node_ids, weak_adjacency)
    strong = strongly_connected_components(node_ids, directed_adjacency)
    undirected_edges = {
        tuple(sorted((lane["from_node_id"], lane["to_node_id"])))
        for lane in lanes_by_id.values()
        if lane["from_node_id"] in nodes_by_id and lane["to_node_id"] in nodes_by_id
    }
    independent_cycle_count = max(0, len(undirected_edges) - len(node_ids) + len(weak))
    if len(weak) != 1:
        verification.fail(
            "connectivity",
            "central-network-not-connected",
            "$.cells[*].directed_lanes",
            (
                "the promoted Central cache must form one connected graph; "
                "summing disconnected certified islands cannot satisfy the "
                "connected multi-block coverage requirement"
            ),
            expected=1,
            actual=len(weak),
        )
    if len(strong) != len(weak):
        verification.fail(
            "connectivity",
            "component-not-strongly-connected",
            "$.cells[*].directed_lanes",
            "every weak component must be independently strongly connected",
            expected=len(weak),
            actual=len(strong),
        )
    verification.metric("connectivity", "weak_component_count", len(weak))
    verification.metric("connectivity", "strong_component_count", len(strong))

    component_diagnostics = []
    for source_index, component in enumerate(weak):
        component_lanes = [
            lane
            for lane in lanes_by_id.values()
            if lane["from_node_id"] in component and lane["to_node_id"] in component
        ]
        component_cells = sorted({node_owner[node_id] for node_id in component})
        component_edges = {
            tuple(sorted((lane["from_node_id"], lane["to_node_id"])))
            for lane in component_lanes
        }
        component_cycle_count = max(
            0,
            len(component_edges) - len(component) + 1,
        )
        declared_component_ids = {
            nodes_by_id[node_id]["component_id"] for node_id in component
        }
        component_diagnostics.append(
            {
                "source_component_index": source_index,
                "component_id": (
                    next(iter(declared_component_ids))
                    if len(declared_component_ids) == 1
                    else None
                ),
                "node_count": len(component),
                "directed_lane_count": len(component_lanes),
                "directional_lane_length_cm": round(
                    sum(float(lane["length_cm"]) for lane in component_lanes), 3
                ),
                "junction_count": sum(
                    nodes_by_id[node_id]["kind"] == "junction"
                    for node_id in component
                ),
                "street_block_count": component_cycle_count,
                "cell_count": len(component_cells),
                "cell_ids": component_cells,
            }
        )
    component_diagnostics.sort(
        key=lambda component: (
            -component["directional_lane_length_cm"],
            -component["node_count"],
            component["cell_ids"],
        )
    )
    for rank, component in enumerate(component_diagnostics, start=1):
        component["size_rank"] = rank
    largest_component = component_diagnostics[0] if component_diagnostics else None
    verification.metric("connectivity", "component_sizes", component_diagnostics)
    verification.metric("connectivity", "largest_component", largest_component)

    declared_components: dict[str, dict[str, Any]] = {}
    declared_node_ids: set[str] = set()
    declared_lane_ids: set[str] = set()
    for component_index, component in enumerate(document["components"]):
        component_path = f"$.components[{component_index}]"
        component_id = component["component_id"]
        if component_id in declared_components:
            verification.fail(
                "connectivity",
                "duplicate-component-id",
                f"{component_path}.component_id",
                "component identifiers must be unique",
                actual=component_id,
            )
            continue
        declared_components[component_id] = component
        component_nodes = set(component["node_ids"])
        component_lanes = set(component["directed_lane_ids"])
        if declared_node_ids.intersection(component_nodes) or declared_lane_ids.intersection(component_lanes):
            verification.fail(
                "connectivity",
                "overlapping-component-membership",
                component_path,
                "component records must form a disjoint graph partition",
            )
        declared_node_ids.update(component_nodes)
        declared_lane_ids.update(component_lanes)
        derived_nodes = {
            node_id
            for node_id, node in nodes_by_id.items()
            if node["component_id"] == component_id
        }
        derived_lanes = {
            lane_id
            for lane_id, lane in lanes_by_id.items()
            if lane["component_id"] == component_id
        }
        if component_nodes != derived_nodes:
            verification.fail(
                "connectivity",
                "component-node-membership-mismatch",
                f"{component_path}.node_ids",
                "declared node_ids must exactly match node component_id values",
                expected=sorted(derived_nodes),
                actual=sorted(component_nodes),
            )
        if component_lanes != derived_lanes:
            verification.fail(
                "connectivity",
                "component-lane-membership-mismatch",
                f"{component_path}.directed_lane_ids",
                "declared lane ids must exactly match lane component_id values",
                expected=sorted(derived_lanes),
                actual=sorted(component_lanes),
            )
        if (
            component_nodes
            and component_lanes
            and component_nodes <= node_ids
            and component_lanes <= set(lanes_by_id)
        ):
            derived_cells = sorted({node_owner[node_id] for node_id in component_nodes})
            derived_origins = sorted(
                {lanes_by_id[lane_id]["topology_origin"] for lane_id in component_lanes}
            )
            derived_length = sum(
                float(lanes_by_id[lane_id]["length_cm"]) for lane_id in component_lanes
            )
            derived_junctions = sum(
                nodes_by_id[node_id]["kind"] == "junction" for node_id in component_nodes
            )
            component_edges = {
                tuple(
                    sorted(
                        (
                            lanes_by_id[lane_id]["from_node_id"],
                            lanes_by_id[lane_id]["to_node_id"],
                        )
                    )
                )
                for lane_id in component_lanes
            }
            derived_blocks = max(0, len(component_edges) - len(component_nodes) + 1)
            comparisons = (
                ("cell_ids", component["cell_ids"], derived_cells),
                ("topology_origins", component["topology_origins"], derived_origins),
                ("junction_count", component["junction_count"], derived_junctions),
                ("street_block_count", component["street_block_count"], derived_blocks),
            )
            for field, actual, expected in comparisons:
                if actual != expected:
                    verification.fail(
                        "connectivity",
                        "component-evidence-mismatch",
                        f"{component_path}.{field}",
                        "component metadata must equal topology-derived evidence",
                        expected=expected,
                        actual=actual,
                    )
            if abs(float(component["directional_lane_length_cm"]) - derived_length) > LENGTH_TOLERANCE_CM:
                verification.fail(
                    "connectivity",
                    "component-length-mismatch",
                    f"{component_path}.directional_lane_length_cm",
                    "component length must equal its directional lane total",
                    expected=derived_length,
                    actual=component["directional_lane_length_cm"],
                )
    if declared_node_ids != node_ids or declared_lane_ids != set(lanes_by_id):
        verification.fail(
            "connectivity",
            "component-partition-incomplete",
            "$.components",
            "declared components must partition every runtime node and lane exactly once",
        )
    derived_component_ids = {
        diagnostic["component_id"] for diagnostic in component_diagnostics
    }
    if None in derived_component_ids or derived_component_ids != set(declared_components):
        verification.fail(
            "connectivity",
            "component-id-topology-mismatch",
            "$.components",
            "each topological component must have one matching explicit component_id",
            expected=sorted(set(declared_components)),
            actual=sorted(value for value in derived_component_ids if value is not None),
        )

    junction_count = sum(node["kind"] == "junction" for node in nodes_by_id.values())
    street_block_count = int(document["evidence"]["street_block_count"])
    certified_geographic_block_count = int(
        document["evidence"]["certified_geographic_block_count"]
    )
    derived_geographic_block_count = sum(
        bool(cell["directed_lanes"]) for cell in document["cells"]
    )
    if len(cells_by_id) < MINIMUM_CELLS:
        verification.fail(
            "coverage",
            "too-few-cells",
            "$.cells",
            "Central cache must retain the six-cell partition",
            expected=f">={MINIMUM_CELLS}",
            actual=len(cells_by_id),
        )
    if street_block_count > independent_cycle_count:
        verification.fail(
            "coverage",
            "street-block-evidence-exceeds-cycle-rank",
            "$.evidence.street_block_count",
            "reported street blocks cannot exceed the graph's independent-cycle count",
            expected=f"<={independent_cycle_count}",
            actual=street_block_count,
        )
    if street_block_count < MINIMUM_CONNECTED_STREET_BLOCKS:
        verification.fail(
            "coverage",
            "too-few-connected-street-blocks",
            "$.evidence.street_block_count",
            "Central cache must contain at least four connected street blocks",
            expected=f">={MINIMUM_CONNECTED_STREET_BLOCKS}",
            actual=street_block_count,
        )
    if certified_geographic_block_count != derived_geographic_block_count:
        verification.fail(
            "coverage",
            "geographic-block-evidence-mismatch",
            "$.evidence.certified_geographic_block_count",
            "geographic block evidence must equal cells containing certified lanes",
            expected=derived_geographic_block_count,
            actual=certified_geographic_block_count,
        )
    if certified_geographic_block_count < MINIMUM_GEOGRAPHIC_BLOCKS:
        verification.fail(
            "coverage",
            "too-few-certified-geographic-blocks",
            "$.evidence.certified_geographic_block_count",
            "Central cache must cover at least four configured geographic blocks",
            expected=f">={MINIMUM_GEOGRAPHIC_BLOCKS}",
            actual=certified_geographic_block_count,
        )
    if junction_count < MINIMUM_JUNCTIONS:
        verification.fail(
            "coverage",
            "too-few-junctions",
            "$.cells[*].nodes",
            "certified topology does not contain eight junction nodes",
            expected=f">={MINIMUM_JUNCTIONS}",
            actual=junction_count,
        )
    if total_lane_length_cm + LENGTH_TOLERANCE_CM < minimum_directional_lane_length_cm:
        verification.fail(
            "coverage",
            "insufficient-directional-lane-length",
            "$.cells[*].directed_lanes[*].length_cm",
            "certified directional lanes total less than three kilometers",
            expected=f">={minimum_directional_lane_length_cm}",
            actual=total_lane_length_cm,
        )
    if largest_component is None or (
        largest_component["directional_lane_length_cm"] + LENGTH_TOLERANCE_CM
        < minimum_directional_lane_length_cm
    ):
        verification.fail(
            "coverage",
            "insufficient-connected-directional-lane-length",
            "$.components",
            (
                "one connected Central component, not a disconnected union, "
                "must contain at least three kilometers of certified directional lanes"
            ),
            expected=f">={minimum_directional_lane_length_cm}",
            actual=(
                largest_component["directional_lane_length_cm"]
                if largest_component is not None
                else 0.0
            ),
        )
    verification.metric("coverage", "cell_count", len(cells_by_id))
    verification.metric("coverage", "street_block_count", street_block_count)
    verification.metric("coverage", "independent_cycle_count", independent_cycle_count)
    verification.metric(
        "coverage",
        "certified_geographic_block_count",
        certified_geographic_block_count,
    )
    verification.metric("coverage", "junction_count", junction_count)
    verification.metric("coverage", "directional_lane_count", len(lanes_by_id))
    verification.metric("coverage", "directional_lane_length_cm", round(total_lane_length_cm, 3))
    verification.metric("coverage", "directional_lane_length_km", round(total_lane_length_cm / 100_000.0, 6))

    component_for_node: dict[str, int] = {}
    component_id_for_index: dict[int, str | None] = {}
    for component_index, component in enumerate(weak):
        identifiers = {nodes_by_id[node_id]["component_id"] for node_id in component}
        component_id_for_index[component_index] = (
            next(iter(identifiers)) if len(identifiers) == 1 else None
        )
        for node_id in component:
            component_for_node[node_id] = component_index

    districts_by_id: dict[str, dict[str, Any]] = {}
    district_components: set[int] = set()
    total_population = 0
    for district_index, district in enumerate(document["spawn_districts"]):
        district_path = f"$.spawn_districts[{district_index}]"
        district_id = district["district_id"]
        if district_id in districts_by_id:
            verification.fail(
                "spawn_districts",
                "duplicate-district-id",
                f"{district_path}.district_id",
                "spawn district identifiers must be unique",
                actual=district_id,
            )
        districts_by_id[district_id] = district
        total_population += int(district["target_population"])
        district_cells = set(district["cell_ids"])
        seed_components: set[int] = set()

        for cell_id in district_cells:
            if cell_id not in cells_by_id:
                verification.fail(
                    "spawn_districts",
                    "missing-district-cell",
                    f"{district_path}.cell_ids",
                    "district references a missing certified cell",
                    actual=cell_id,
                )
        for node_id in district["spawn_node_ids"]:
            if node_id not in nodes_by_id:
                verification.fail(
                    "spawn_districts",
                    "missing-spawn-node",
                    f"{district_path}.spawn_node_ids",
                    "district references a missing spawn node",
                    actual=node_id,
                )
                continue
            if node_owner[node_id] not in district_cells:
                verification.fail(
                    "spawn_districts",
                    "spawn-node-outside-district-cells",
                    f"{district_path}.spawn_node_ids",
                    "spawn node must be owned by a configured district cell",
                    actual=node_id,
                )
            seed_components.add(component_for_node[node_id])
        for lane_id in district["spawn_lane_ids"]:
            if lane_id not in lanes_by_id:
                verification.fail(
                    "spawn_districts",
                    "missing-spawn-lane",
                    f"{district_path}.spawn_lane_ids",
                    "district references a missing certified spawn lane",
                    actual=lane_id,
                )
                continue
            if lane_owner[lane_id] not in district_cells:
                verification.fail(
                    "spawn_districts",
                    "spawn-lane-outside-district-cells",
                    f"{district_path}.spawn_lane_ids",
                    "spawn lane must be owned by a configured district cell",
                    actual=lane_id,
                )
            seed_components.add(component_for_node[lanes_by_id[lane_id]["from_node_id"]])
        if len(seed_components) != 1:
            verification.fail(
                "spawn_districts",
                "district-seeds-not-connected",
                district_path,
                "all spawn nodes and lanes in a district must share one network component",
                actual=sorted(seed_components),
            )
        elif component_id_for_index[next(iter(seed_components))] != district["component_id"]:
            verification.fail(
                "spawn_districts",
                "district-component-id-mismatch",
                f"{district_path}.component_id",
                "district seeds must remain inside their declared certified component",
                expected=component_id_for_index[next(iter(seed_components))],
                actual=district["component_id"],
            )
        district_components.update(seed_components)

    if len(districts_by_id) < MINIMUM_SPAWN_DISTRICTS:
        verification.fail(
            "spawn_districts",
            "too-few-spawn-districts",
            "$.spawn_districts",
            "Central cache requires at least six component-local spawn districts",
            expected=f">={MINIMUM_SPAWN_DISTRICTS}",
            actual=len(districts_by_id),
        )
    if len(district_components) != 1:
        verification.fail(
            "spawn_districts",
            "spawn-districts-not-connected-to-one-graph",
            "$.spawn_districts",
            (
                "all six spawn districts must have a certified path through "
                "the same connected Central graph"
            ),
            expected=1,
            actual=len(district_components),
        )
    if total_population != TARGET_POPULATION:
        verification.fail(
            "population",
            "configured-population-mismatch",
            "$.spawn_districts[*].target_population",
            "enabled district targets must sum to exactly 300",
            expected=TARGET_POPULATION,
            actual=total_population,
        )
    verification.metric("spawn_districts", "component_local_spawn_district_count", len(districts_by_id))
    verification.metric("spawn_districts", "district_component_count", len(district_components))
    verification.metric("population", "configured_population", total_population)

    rejection_keys = (
        "missing_support_rejection_count",
        "first_blocker_rejection_count",
        "height_continuity_rejection_count",
        "slope_rejection_count",
        "multi_track_rejection_count",
        "capsule_clearance_rejection_count",
    )
    pass_keys = (
        "exact_xy_support_pass_count",
        "first_blocker_pass_count",
        "height_continuity_pass_count",
        "slope_pass_count",
        "multi_track_support_pass_count",
        "capsule_clearance_pass_count",
    )

    def verify_evidence(
        evidence: dict[str, Any],
        path: str,
        actual_lane_count: int,
        actual_sample_count: int,
        actual_portal_count: int,
        actual_junction_count: int,
        actual_length_cm: float,
    ) -> None:
        if evidence["candidate_lane_count"] != evidence["certified_lane_count"] + evidence["rejected_lane_count"]:
            verification.fail(
                "evidence",
                "candidate-resolution-count-mismatch",
                f"{path}.candidate_lane_count",
                "every candidate lane must resolve to certified or rejected",
            )
        if evidence["certified_lane_count"] != actual_lane_count:
            verification.fail(
                "evidence",
                "certified-lane-evidence-mismatch",
                f"{path}.certified_lane_count",
                "evidence must equal stored certified lane count",
                expected=actual_lane_count,
                actual=evidence["certified_lane_count"],
            )
        if sum(int(evidence[key]) for key in rejection_keys) != evidence["rejected_lane_count"]:
            verification.fail(
                "evidence",
                "rejection-reason-count-mismatch",
                path,
                "rejection reason counters must partition all rejected candidates",
            )
        if evidence["strict_ground_sample_count"] < actual_sample_count:
            verification.fail(
                "evidence",
                "strict-sample-evidence-underflow",
                f"{path}.strict_ground_sample_count",
                "strict sample count cannot be lower than stored accepted samples",
                expected=f">={actual_sample_count}",
                actual=evidence["strict_ground_sample_count"],
            )
        for key in pass_keys:
            if evidence[key] < actual_sample_count:
                verification.fail(
                    "evidence",
                    "certification-pass-count-underflow",
                    f"{path}.{key}",
                    "every stored sample must have passed this certification stage",
                    expected=f">={actual_sample_count}",
                    actual=evidence[key],
                )
        if evidence["portal_count"] != actual_portal_count:
            verification.fail(
                "evidence",
                "portal-evidence-mismatch",
                f"{path}.portal_count",
                "portal evidence must equal stored portal count",
                expected=actual_portal_count,
                actual=evidence["portal_count"],
            )
        if evidence["junction_count"] != actual_junction_count:
            verification.fail(
                "evidence",
                "junction-evidence-mismatch",
                f"{path}.junction_count",
                "junction evidence must equal stored junction count",
                expected=actual_junction_count,
                actual=evidence["junction_count"],
            )
        if abs(float(evidence["certified_directional_lane_length_cm"]) - actual_length_cm) > LENGTH_TOLERANCE_CM:
            verification.fail(
                "evidence",
                "lane-length-evidence-mismatch",
                f"{path}.certified_directional_lane_length_cm",
                "lane-length evidence must equal the stored directional lane total",
                expected=actual_length_cm,
                actual=evidence["certified_directional_lane_length_cm"],
            )

    cell_candidate_total = 0
    cell_rejected_total = 0
    for cell_index, cell in enumerate(document["cells"]):
        cell_lanes = cell["directed_lanes"]
        cell_samples = sum(len(lane["ground_samples"]) for lane in cell_lanes)
        cell_junctions = sum(node["kind"] == "junction" for node in cell["nodes"])
        cell_length = sum(float(lane["length_cm"]) for lane in cell_lanes)
        verify_evidence(
            cell["evidence"],
            f"$.cells[{cell_index}].evidence",
            len(cell_lanes),
            cell_samples,
            len(cell["portals"]),
            cell_junctions,
            cell_length,
        )
        cell_candidate_total += int(cell["evidence"]["candidate_lane_count"])
        cell_rejected_total += int(cell["evidence"]["rejected_lane_count"])

    root_evidence = document["evidence"]
    verify_evidence(
        root_evidence,
        "$.evidence",
        len(lanes_by_id),
        total_ground_samples,
        len(portals_by_id),
        junction_count,
        total_lane_length_cm,
    )
    if root_evidence["candidate_lane_count"] != cell_candidate_total:
        verification.fail(
            "evidence",
            "root-cell-candidate-evidence-mismatch",
            "$.evidence.candidate_lane_count",
            "root candidate count must equal independently rebuildable cell totals",
            expected=cell_candidate_total,
            actual=root_evidence["candidate_lane_count"],
        )
    if root_evidence["rejected_lane_count"] != cell_rejected_total:
        verification.fail(
            "evidence",
            "root-cell-rejected-evidence-mismatch",
            "$.evidence.rejected_lane_count",
            "root rejected count must equal independently rebuildable cell totals",
            expected=cell_rejected_total,
            actual=root_evidence["rejected_lane_count"],
        )
    expected_root_fields = {
        "connected_component_count": len(weak),
        "junction_count": junction_count,
        "portal_count": len(portals_by_id),
        "spawn_district_count": len(districts_by_id),
    }
    for key, expected in expected_root_fields.items():
        if root_evidence[key] != expected:
            verification.fail(
                "evidence",
                "root-derived-evidence-mismatch",
                f"$.evidence.{key}",
                "root evidence does not match topology-derived value",
                expected=expected,
                actual=root_evidence[key],
            )

    verification.metric("evidence", "ground_sample_count", total_ground_samples)
    verification.metric("evidence", "cell_evidence_records", len(document["cells"]))

    return {
        "cells": cells_by_id,
        "nodes": nodes_by_id,
        "lanes": lanes_by_id,
        "portals": portals_by_id,
        "weak_components": weak,
        "strong_components": strong,
        "component_diagnostics": component_diagnostics,
        "largest_component": largest_component,
        "junction_count": junction_count,
        "total_lane_length_cm": total_lane_length_cm,
        "total_population": total_population,
    }


def verify_source_provenance(
    verification: Verification,
    document: dict[str, Any],
    source_document: dict[str, Any] | None,
    source_schema: dict[str, Any] | None,
    network_info: dict[str, Any],
) -> None:
    verification.execute("source_provenance")
    verification.metric(
        "source_provenance",
        "certified_component_sizes",
        network_info["component_diagnostics"],
    )
    verification.metric(
        "source_provenance",
        "certified_largest_component",
        network_info["largest_component"],
    )
    provenance = document.get("source_provenance")
    if provenance is not None and provenance["topology_sha256"] != document["hashes"]["topology_sha256"]:
        verification.fail(
            "source_provenance",
            "cache-provenance-topology-mismatch",
            "$.source_provenance.topology_sha256",
            "cache provenance and compatibility topology hashes must match",
            expected=document["hashes"]["topology_sha256"],
            actual=provenance["topology_sha256"],
        )

    if source_document is None:
        verification.metric("source_provenance", "source_document_checked", False)
        return
    if provenance is None:
        verification.fail(
            "source_provenance",
            "missing-source-provenance",
            "$.source_provenance",
            "--source requires a source_provenance record in the certified mirror",
        )
        return

    if source_schema is not None:
        source_validator = Draft202012Validator(source_schema, format_checker=FormatChecker())
        for error in sorted(source_validator.iter_errors(source_document), key=lambda item: list(item.absolute_path)):
            verification.fail(
                "source_provenance",
                "source-schema-violation",
                "source:" + json_path(error.absolute_path),
                error.message,
            )

    comparisons = {
        "dataset_id": source_document.get("dataset_id"),
        "topology_sha256": source_document.get("hashes", {}).get("topology_sha256"),
        "document_sha256": source_document.get("hashes", {}).get("document_sha256"),
    }
    for key, source_value in comparisons.items():
        if provenance.get(key) != source_value:
            verification.fail(
                "source_provenance",
                "source-provenance-mismatch",
                f"$.source_provenance.{key}",
                "certified cache provenance does not match the supplied source mirror",
                expected=source_value,
                actual=provenance.get(key),
            )
    verification.metric("source_provenance", "source_document_checked", True)


def build_report(
    verification: Verification,
    input_path: Path | None,
    input_digest: str | None,
    schema_path: Path,
    source_path: Path | None,
    minimum_directional_lane_length_cm: float = MINIMUM_DIRECTIONAL_LANE_LENGTH_CM,
) -> dict[str, Any]:
    verification.finalize()
    summary = {
        "required_check_count": len(CHECK_NAMES),
        "passed_check_count": sum(check["passed"] is True for check in verification.checks.values()),
        "failed_check_count": sum(check["passed"] is False for check in verification.checks.values()),
        "unexecuted_check_count": sum(check["passed"] is None for check in verification.checks.values()),
        "issue_count": len(verification.issues),
    }
    return {
        "schema_version": 1,
        "verifier": {
            "name": "verify_central_network_cache",
            "version": VERIFIER_VERSION,
        },
        "generated_at_utc": utc_now(),
        "input": {
            "cache_path": str(input_path) if input_path is not None else None,
            "cache_sha256": input_digest,
            "schema_path": str(schema_path),
            "source_path": str(source_path) if source_path is not None else None,
        },
        "thresholds": {
            "minimum_cells": MINIMUM_CELLS,
            "minimum_certified_geographic_blocks": MINIMUM_GEOGRAPHIC_BLOCKS,
            "minimum_connected_street_blocks": MINIMUM_CONNECTED_STREET_BLOCKS,
            "minimum_junctions": MINIMUM_JUNCTIONS,
            "minimum_directional_lane_length_cm": minimum_directional_lane_length_cm,
            "minimum_component_local_spawn_districts": MINIMUM_SPAWN_DISTRICTS,
            "target_population": TARGET_POPULATION,
        },
        "overall_passed": verification.overall_passed,
        "summary": summary,
        "checks": verification.checks,
        "issues": verification.issues,
    }


def verify_document(
    document: Any,
    schema: dict[str, Any],
    *,
    input_path: Path | None = None,
    input_digest: str | None = None,
    schema_path: Path = DEFAULT_SCHEMA_PATH,
    source_document: dict[str, Any] | None = None,
    source_schema: dict[str, Any] | None = None,
    source_path: Path | None = None,
    minimum_directional_lane_length_cm: float = MINIMUM_DIRECTIONAL_LANE_LENGTH_CM,
) -> dict[str, Any]:
    verification = Verification(input_path)
    schema_valid = validate_against_schema(verification, document, schema)
    if schema_valid:
        verify_hashes(verification, document)
        network_info = verify_network(
            verification,
            document,
            minimum_directional_lane_length_cm=minimum_directional_lane_length_cm,
        )
        verify_source_provenance(
            verification,
            document,
            source_document,
            source_schema,
            network_info,
        )
    else:
        for check in CHECK_NAMES:
            if check != "schema":
                verification.fail(
                    check,
                    "not-executed-after-schema-failure",
                    "$",
                    "semantic verification was not executed because the certified mirror schema failed",
                )
    return build_report(
        verification,
        input_path,
        input_digest,
        schema_path,
        source_path,
        minimum_directional_lane_length_cm,
    )


def write_report(report: dict[str, Any], explicit_path: Path | None) -> list[Path]:
    payload = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if explicit_path is not None:
        explicit_path.parent.mkdir(parents=True, exist_ok=True)
        explicit_path.write_text(payload, encoding="utf-8")
        return [explicit_path]

    DEFAULT_REPORT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    timestamped = DEFAULT_REPORT_DIR / f"central_network_cache_verification_{stamp}.json"
    latest = DEFAULT_REPORT_DIR / "central_network_cache_verification_latest.json"
    timestamped.write_text(payload, encoding="utf-8")
    latest.write_text(payload, encoding="utf-8")
    return [timestamped, latest]


def zero_hash() -> str:
    return "0" * 64


def make_evidence(
    lanes: int,
    samples: int,
    portals: int,
    junctions: int,
    length_cm: float,
    *,
    blocks: int = 0,
    geographic_blocks: int | None = None,
    districts: int = 0,
    components: int = 0,
) -> dict[str, Any]:
    evidence = {
        "candidate_lane_count": lanes,
        "certified_lane_count": lanes,
        "rejected_lane_count": 0,
        "coarse_support_check_count": max(lanes, samples),
        "strict_ground_sample_count": samples,
        "exact_xy_support_pass_count": samples,
        "first_blocker_pass_count": samples,
        "height_continuity_pass_count": samples,
        "slope_pass_count": samples,
        "multi_track_support_pass_count": samples,
        "capsule_clearance_pass_count": samples,
        "missing_support_rejection_count": 0,
        "first_blocker_rejection_count": 0,
        "height_continuity_rejection_count": 0,
        "slope_rejection_count": 0,
        "multi_track_rejection_count": 0,
        "capsule_clearance_rejection_count": 0,
        "connected_component_count": components,
        "street_block_count": blocks,
        "certified_geographic_block_count": (
            (1 if lanes else 0)
            if geographic_blocks is None
            else geographic_blocks
        ),
        "junction_count": junctions,
        "portal_count": portals,
        "spawn_district_count": districts,
        "certified_directional_lane_length_cm": length_cm,
        "whole_area_recertification_count": 0,
    }
    return evidence


def stamp_fixture_hashes(document: dict[str, Any]) -> None:
    root_hashes = document["hashes"]
    for cell in document["cells"]:
        for key in HASH_INPUT_KEYS:
            cell["hashes"][key] = root_hashes[key]
        content_hash, combined_hash = expected_cell_hashes(cell, root_hashes)
        cell["hashes"]["cell_content_sha256"] = content_hash
        cell["hashes"]["combined_sha256"] = combined_hash
    root_content, root_combined = expected_root_hashes(document)
    root_hashes["cell_content_sha256"] = root_content
    root_hashes["combined_sha256"] = root_combined


def build_self_test_fixture() -> dict[str, Any]:
    component_id = "certified-component-fixture"
    compatibility = {
        "algorithm": "SHA-256",
        "topology_sha256": hashlib.sha256(b"fixture-topology").hexdigest(),
        "georeference_sha256": hashlib.sha256(b"fixture-georeference").hexdigest(),
        "tileset_sha256": hashlib.sha256(b"fixture-tileset").hexdigest(),
        "collision_settings_sha256": hashlib.sha256(b"fixture-collision").hexdigest(),
        "cell_content_sha256": zero_hash(),
        "combined_sha256": zero_hash(),
    }
    cells: list[dict[str, Any]] = []
    node_positions: dict[str, list[float]] = {}
    for cell_index in range(6):
        x = float(cell_index * 20)
        node_positions[f"node-{cell_index}-a"] = [x, 0.0, 100.0]
        node_positions[f"node-{cell_index}-b"] = [x + 5.0, 0.0, 100.0]
        cells.append(
            {
                "schema_version": SCHEMA_VERSION,
                "cell_id": f"central-r{cell_index // 3}-c{cell_index % 3}",
                "grid_coordinate": [cell_index % 3, cell_index // 3],
                "world_bounds": {
                    "min": [x - 1.0, -100.0, 0.0],
                    "max": [x + 6.0, 100.0, 500.0],
                },
                "source_feature_ids": [f"fixture-feature-{cell_index}-local", f"fixture-feature-{cell_index}-portal"],
                "nodes": [
                    {
                        "node_id": f"node-{cell_index}-a",
                        "cell_id": f"central-r{cell_index // 3}-c{cell_index % 3}",
                        "component_id": component_id,
                        "kind": "junction",
                        "position": node_positions[f"node-{cell_index}-a"],
                        "incoming_lane_ids": [],
                        "outgoing_lane_ids": [],
                    },
                    {
                        "node_id": f"node-{cell_index}-b",
                        "cell_id": f"central-r{cell_index // 3}-c{cell_index % 3}",
                        "component_id": component_id,
                        "kind": "junction",
                        "position": node_positions[f"node-{cell_index}-b"],
                        "incoming_lane_ids": [],
                        "outgoing_lane_ids": [],
                    },
                ],
                "directed_lanes": [],
                "portals": [],
                "hashes": copy.deepcopy(compatibility),
                "evidence": {},
                "certified": True,
            }
        )

    nodes_by_id = {node["node_id"]: node for cell in cells for node in cell["nodes"]}
    cell_by_id = {cell["cell_id"]: cell for cell in cells}

    def add_lane_pair(
        first_node_id: str,
        second_node_id: str,
        first_cell: dict[str, Any],
        second_cell: dict[str, Any],
        feature_id: str,
        pair_id: str,
    ) -> None:
        for from_id, to_id, owner, suffix, reverse_suffix in (
            (first_node_id, second_node_id, first_cell, "ab", "ba"),
            (second_node_id, first_node_id, second_cell, "ba", "ab"),
        ):
            lane_id = f"lane-{pair_id}-{suffix}"
            reverse_id = f"lane-{pair_id}-{reverse_suffix}"
            from_position = nodes_by_id[from_id]["position"]
            to_position = nodes_by_id[to_id]["position"]
            lane_length = vector_distance(from_position, to_position)
            segment_count = max(
                1,
                math.ceil(lane_length / MAXIMUM_SUPPORT_SPACING_CM),
            )
            ground_samples = []
            for sample_index in range(segment_count + 1):
                alpha = sample_index / segment_count
                center_position = [
                    float(from_position[axis])
                    + (float(to_position[axis]) - float(from_position[axis])) * alpha
                    for axis in range(3)
                ]
                ground_samples.append(
                    {
                        "sample_id": f"sample-{lane_id}-{sample_index}",
                        "sample_index": sample_index,
                        "distance_along_lane_cm": lane_length * alpha,
                        "center_position": center_position,
                        "left_track_position": [
                            center_position[0],
                            center_position[1] - 50.0,
                            center_position[2],
                        ],
                        "right_track_position": [
                            center_position[0],
                            center_position[1] + 50.0,
                            center_position[2],
                        ],
                        "surface_normal": [0.0, 0.0, 1.0],
                        "surface_slope_degrees": 0.0,
                        "max_neighbor_height_delta_cm": 0.0,
                        "supporting_primitive_id": "fixture-cesium-primitive",
                        "evidence_mask": FULL_GROUND_EVIDENCE_MASK,
                    }
                )
            if feature_id not in owner["source_feature_ids"]:
                owner["source_feature_ids"].append(feature_id)
            lane = {
                "lane_id": lane_id,
                "cell_id": owner["cell_id"],
                "component_id": component_id,
                "source_feature_id": feature_id,
                "from_node_id": from_id,
                "to_node_id": to_id,
                "reverse_lane_id": reverse_id,
                "topology_origin": "openstreetmap",
                "pedestrian_class": "sidewalk",
                "width_cm": 150.0,
                "length_cm": lane_length,
                "certified": True,
                "ground_samples": ground_samples,
            }
            owner["directed_lanes"].append(lane)
            nodes_by_id[from_id]["outgoing_lane_ids"].append(lane_id)
            nodes_by_id[to_id]["incoming_lane_ids"].append(lane_id)

        if first_cell["cell_id"] != second_cell["cell_id"]:
            first_portal_id = f"portal-{pair_id}-ab"
            second_portal_id = f"portal-{pair_id}-ba"
            first_cell["portals"].append(
                {
                    "portal_id": first_portal_id,
                    "reverse_portal_id": second_portal_id,
                    "local_cell_id": first_cell["cell_id"],
                    "remote_cell_id": second_cell["cell_id"],
                    "local_node_id": first_node_id,
                    "remote_node_id": second_node_id,
                    "directed_lane_id": f"lane-{pair_id}-ab",
                    "position": node_positions[first_node_id],
                    "certified": True,
                }
            )
            second_cell["portals"].append(
                {
                    "portal_id": second_portal_id,
                    "reverse_portal_id": first_portal_id,
                    "local_cell_id": second_cell["cell_id"],
                    "remote_cell_id": first_cell["cell_id"],
                    "local_node_id": second_node_id,
                    "remote_node_id": first_node_id,
                    "directed_lane_id": f"lane-{pair_id}-ba",
                    "position": node_positions[second_node_id],
                    "certified": True,
                }
            )

    for cell_index, cell in enumerate(cells):
        add_lane_pair(
            f"node-{cell_index}-a",
            f"node-{cell_index}-b",
            cell,
            cell,
            f"fixture-feature-{cell_index}-local",
            f"local-{cell_index}",
        )
        next_index = (cell_index + 1) % len(cells)
        next_cell = cells[next_index]
        add_lane_pair(
            f"node-{cell_index}-b",
            f"node-{next_index}-a",
            cell,
            next_cell,
            f"fixture-feature-{cell_index}-portal",
            f"portal-{cell_index}-{next_index}",
        )

    # Three reviewed cross-cell chords raise the undirected graph cycle rank
    # from one perimeter loop to four independently verifiable block cycles.
    for chord_index, (first_index, second_index) in enumerate(((0, 2), (1, 3), (2, 4))):
        add_lane_pair(
            f"node-{first_index}-a",
            f"node-{second_index}-a",
            cells[first_index],
            cells[second_index],
            f"fixture-feature-chord-{chord_index}",
            f"chord-{chord_index}",
        )

    total_lanes = 0
    total_samples = 0
    total_portals = 0
    total_length = 0.0
    for cell in cells:
        lanes = len(cell["directed_lanes"])
        samples = sum(len(lane["ground_samples"]) for lane in cell["directed_lanes"])
        portals = len(cell["portals"])
        length = sum(lane["length_cm"] for lane in cell["directed_lanes"])
        cell["evidence"] = make_evidence(lanes, samples, portals, 2, length)
        total_lanes += lanes
        total_samples += samples
        total_portals += portals
        total_length += length

    districts = []
    for index, cell in enumerate(cells):
        districts.append(
            {
                "district_id": f"district-{index}",
                "component_id": component_id,
                "cell_ids": [cell["cell_id"]],
                "spawn_node_ids": [f"node-{index}-a"],
                "spawn_lane_ids": [nodes_by_id[f"node-{index}-a"]["outgoing_lane_ids"][0]],
                "world_bounds": cell["world_bounds"],
                "target_population": 50,
                "selection_weight": 1.0,
                "enabled": True,
            }
        )

    document = {
        "$schema": "../central_network_certified.schema.json",
        "schema_version": SCHEMA_VERSION,
        "network_id": "central-self-test-v1",
        "build_id": "12345678-1234-1234-1234-123456789abc",
        "generator_version": "self-test-1",
        "world_bounds": {"min": [-1.0, -100.0, 0.0], "max": [106.0, 100.0, 500.0]},
        "hashes": compatibility,
        "cells": cells,
        "components": [
            {
                "component_id": component_id,
                "cell_ids": sorted(cell["cell_id"] for cell in cells),
                "node_ids": sorted(nodes_by_id),
                "directed_lane_ids": sorted(
                    lane["lane_id"] for cell in cells for lane in cell["directed_lanes"]
                ),
                "topology_origins": ["openstreetmap"],
                "directional_lane_length_cm": total_length,
                "junction_count": 12,
                "street_block_count": 4,
                "certified": True,
            }
        ],
        "spawn_districts": districts,
        "evidence": make_evidence(
            total_lanes,
            total_samples,
            total_portals,
            12,
            total_length,
            blocks=4,
            geographic_blocks=len(cells),
            districts=6,
            components=1,
        ),
    }
    stamp_fixture_hashes(document)
    return document


def run_self_test(schema: dict[str, Any]) -> bool:
    valid = build_self_test_fixture()
    fixture_lane_threshold_cm = 300.0
    valid_report = verify_document(
        valid,
        schema,
        minimum_directional_lane_length_cm=fixture_lane_threshold_cm,
    )

    tampered = copy.deepcopy(valid)
    tampered["cells"][0]["directed_lanes"][0]["length_cm"] += 1.0
    tampered_report = verify_document(
        tampered,
        schema,
        minimum_directional_lane_length_cm=fixture_lane_threshold_cm,
    )

    broken_portal = copy.deepcopy(valid)
    broken_portal["cells"][0]["portals"][0]["remote_node_id"] = "node-does-not-exist"
    stamp_fixture_hashes(broken_portal)
    broken_portal_report = verify_document(
        broken_portal,
        schema,
        minimum_directional_lane_length_cm=fixture_lane_threshold_cm,
    )

    unsafe_normal = copy.deepcopy(valid)
    unsafe_sample = unsafe_normal["cells"][0]["directed_lanes"][0]["ground_samples"][0]
    unsafe_sample["surface_normal"] = [0.871722, 0.0, 0.49]
    stamp_fixture_hashes(unsafe_normal)
    unsafe_normal_report = verify_document(
        unsafe_normal,
        schema,
        minimum_directional_lane_length_cm=fixture_lane_threshold_cm,
    )

    results = {
        "valid_fixture_passed": valid_report["overall_passed"],
        "tampered_hash_rejected": not tampered_report["overall_passed"]
        and any(issue["check"] == "hashes" for issue in tampered_report["issues"]),
        "broken_portal_rejected": not broken_portal_report["overall_passed"]
        and any(issue["check"] == "portals" for issue in broken_portal_report["issues"]),
        "unsafe_surface_normal_rejected": not unsafe_normal_report["overall_passed"]
        and any(
            issue.get("code") == "invalid-surface-normal"
            for issue in unsafe_normal_report["issues"]
        ),
    }
    passed = all(results.values())
    print(
        "CENTRAL_NETWORK_CACHE_VERIFY_SELF_TEST="
        + json.dumps({"passed": passed, "results": results}, sort_keys=True)
    )
    if not passed:
        print(
            json.dumps(
                {
                    "valid": valid_report,
                    "tampered": tampered_report,
                    "portal": broken_portal_report,
                    "unsafe_normal": unsafe_normal_report,
                },
                indent=2,
            )
        )
    return passed


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "cache",
        nargs="?",
        type=Path,
        default=DEFAULT_CACHE_PATH,
        help=f"certified cache audit mirror (default: {DEFAULT_CACHE_PATH})",
    )
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA_PATH)
    parser.add_argument("--source", type=Path, help="optional schema-valid pedestrian source mirror")
    parser.add_argument("--source-schema", type=Path, default=DEFAULT_SOURCE_SCHEMA_PATH)
    parser.add_argument("--report", type=Path, help="write one report instead of timestamped/latest reports")
    parser.add_argument("--self-test", action="store_true", help="run in-memory pass/fail fixtures")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    try:
        schema = load_json(args.schema)
    except Exception as error:
        print(f"CENTRAL_NETWORK_CACHE_VERIFY_ERROR=failed to load schema: {error}", file=sys.stderr)
        return 2

    if args.self_test:
        return 0 if run_self_test(schema) else 1

    input_digest: str | None = None
    source_document = None
    source_schema = None
    try:
        input_digest = file_sha256(args.cache)
        document = load_json(args.cache)
        if args.source is not None:
            source_document = load_json(args.source)
            source_schema = load_json(args.source_schema)
        report = verify_document(
            document,
            schema,
            input_path=args.cache.resolve(),
            input_digest=input_digest,
            schema_path=args.schema.resolve(),
            source_document=source_document,
            source_schema=source_schema,
            source_path=args.source.resolve() if args.source is not None else None,
        )
    except Exception as error:
        verification = Verification(args.cache)
        verification.fail("schema", "input-load-failure", "$", repr(error))
        for check in CHECK_NAMES:
            if check != "schema":
                verification.fail(check, "not-executed-after-input-failure", "$", "input could not be loaded")
        report = build_report(
            verification,
            args.cache.resolve(),
            input_digest,
            args.schema.resolve(),
            args.source.resolve() if args.source is not None else None,
        )

    paths = write_report(report, args.report.resolve() if args.report is not None else None)
    marker = {
        "overall_passed": report["overall_passed"],
        "issue_count": report["summary"]["issue_count"],
        "cache": str(args.cache.resolve()),
        "reports": [str(path.resolve()) for path in paths],
    }
    print("CENTRAL_NETWORK_CACHE_VERIFY=" + json.dumps(marker, ensure_ascii=False, sort_keys=True))
    return 0 if report["overall_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
