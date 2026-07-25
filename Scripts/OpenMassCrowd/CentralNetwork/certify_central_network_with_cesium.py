#!/usr/bin/env python3
"""Build a fail-closed, collision-certified Central pedestrian network.

Host usage::

    python certify_central_network_with_cesium.py dry-run
    python certify_central_network_with_cesium.py status
    python certify_central_network_with_cesium.py self-test

Live usage (with ``/Game/Maps/shanghai`` open)::

    python Scripts/OpenMassCrowd/run_unreal_python_via_mcp.py \
      --file Scripts/OpenMassCrowd/CentralNetwork/certify_central_network_with_cesium.py

The live runner is deliberately incremental.  It fixes the editor viewport over
one source cell, waits for the query-enabled Cesium primitive set to stabilize,
and advances a bounded number of trace/sweep operations on each Slate tick.
Completed candidates are atomically checkpointed, so an editor restart resumes
instead of silently promoting partial work.

Promotion policy
----------------
``central_network_certified.json`` is never written directly.  A pending mirror
is built only after every selected source candidate has resolved.  The pending
mirror must pass ``verify_central_network_cache.py`` (including source
provenance, hashes, connectivity, portals, six 50-person districts and the
300-person total) before an atomic replace makes it the runtime cache.

No feature carrying ``requires_manual_review=true`` is admitted unless an
explicit, accepted correction names that exact feature.  Collision success is
not treated as review approval.
"""

from __future__ import annotations

import argparse
import builtins
import copy
import hashlib
import heapq
import importlib.util
import json
import math
import os
import shutil
import stat
import subprocess
import sys
import time
import uuid
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Iterable, Iterator

try:  # Unreal's embedded interpreter provides this; host tests do not.
    import unreal  # type: ignore
except ImportError:  # pragma: no cover - exercised by all host tests
    unreal = None


SCHEMA_VERSION = 1
CERTIFIED_SCHEMA_VERSION = 2
GENERATOR_VERSION = "1.1.0"
FULL_GROUND_EVIDENCE_MASK = 63
EXPECTED_WORLD = "shanghai"
CELL_COUNT = 6
DISTRICT_POPULATION = 50
TARGET_POPULATION = 300
MINIMUM_TRUSTED_UNDIRECTED_LENGTH_CM = 150_000.0
MINIMUM_CONNECTED_STREET_BLOCKS = 4
MINIMUM_CERTIFIED_JUNCTIONS = 8
TRUSTED_RUNTIME_TOPOLOGY_ORIGINS = frozenset(
    {"openstreetmap", "osm-semantic-recovery"}
)
REVIEWABLE_RUNTIME_TOPOLOGY_ORIGINS = frozenset(
    {"generated-connector", "manual-cesium-connector"}
)
RUNTIME_ELIGIBLE_TOPOLOGY_ORIGINS = (
    TRUSTED_RUNTIME_TOPOLOGY_ORIGINS | REVIEWABLE_RUNTIME_TOPOLOGY_ORIGINS
)

if unreal is not None:  # MCP executes file contents from Saved/PythonTemp.
    _PROJECT_ROOT = Path(
        unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_dir())
    )
    SCRIPT_DIR = _PROJECT_ROOT / "Scripts" / "OpenMassCrowd" / "CentralNetwork"
else:
    _PROJECT_ROOT = Path(__file__).resolve().parents[3]
    SCRIPT_DIR = Path(__file__).resolve().parent
DATA_DIR = SCRIPT_DIR / "Data"
PROJECTED_SOURCE_PATH = DATA_DIR / "central_pedestrian_source_unreal.json"
SOURCE_PATH = DATA_DIR / "central_pedestrian_source.json"
CORRECTIONS_PATH = SCRIPT_DIR / "central_manual_corrections.json"
WORK_PATH = DATA_DIR / "central_network_certification_working.json"
PENDING_PATH = DATA_DIR / "central_network_certified.pending.json"
FINAL_PATH = DATA_DIR / "central_network_certified.json"
AUDIT_PATH = DATA_DIR / "central_network_certification.audit.json"
SCHEMA_PATH = SCRIPT_DIR / "central_network_certified.schema.json"
VERIFIER_PATH = SCRIPT_DIR.parent / "verify_central_network_cache.py"
SOURCE_SCHEMA_PATH = SCRIPT_DIR / "central_pedestrian_source.schema.json"

REJECTION_REASONS = (
    "missing_support",
    "first_blocker",
    "height_continuity",
    "slope",
    "multi_track",
    "capsule_clearance",
)
REJECTION_EVIDENCE_KEYS = {
    "missing_support": "missing_support_rejection_count",
    "first_blocker": "first_blocker_rejection_count",
    "height_continuity": "height_continuity_rejection_count",
    "slope": "slope_rejection_count",
    "multi_track": "multi_track_rejection_count",
    "capsule_clearance": "capsule_clearance_rejection_count",
}

DEFAULT_SETTINGS: dict[str, Any] = {
    "support_spacing_cm": 10.0,
    "coarse_spacing_cm": 100.0,
    "lane_height_offset_cm": 2.0,
    # The photogrammetry sidewalks are often only 0.8-1.0 m clear between
    # facade/curb artifacts. A 0.8 m certified lane still leaves 10 cm either
    # side of the 60 cm pedestrian capsule and is widened only where the
    # crossing/pedestrian-zone source class explicitly permits it.
    "lane_width_cm": 80.0,
    "pedestrian_radius_cm": 30.0,
    "pedestrian_half_height_cm": 82.0,
    "pedestrian_clearance_above_ground_cm": 14.0,
    "trace_height_cm": 1600.0,
    "trace_depth_cm": 2600.0,
    # The Google mesh in this level carries an observed ~14 m vertical datum
    # offset from the source ellipsoid positions. Seed traces may bridge that
    # datum once; all subsequent 10 cm samples retain the strict 90 cm check.
    "seed_height_tolerance_cm": 2000.0,
    "ground_height_tolerance_cm": 90.0,
    "max_neighbor_height_delta_cm": 55.0,
    "max_grade": 0.96,
    # Raw photogrammetry triangle normals are noisy. The decisive slope gate is
    # the independently sampled 10 cm height grade (max_grade); retain a 60°
    # raw-normal guard to reject walls without discarding flat but noisy road.
    "min_walkable_normal_z": 0.5,
    "component_bounds_padding_cm": 5.0,
    "world_channel_match_tolerance_cm": 2.0,
    "operations_per_tick": 64,
    "checkpoint_candidate_interval": 8,
    "streaming_stable_ticks": 12,
    "streaming_min_wait_ticks": 30,
    # Google Photorealistic 3D Tiles can take well over 15 seconds to finish
    # refining a cold Central cell. Keep certification asynchronous, but allow
    # two minutes for the cell-scoped query component set to settle.
    "streaming_timeout_ticks": 7200,
    "max_candidate_streaming_restarts": 12,
    # A 110 m nadir view covers the roughly 150 x 150 m cell without hopping
    # between candidates, while still requesting useful collision LOD.
    "camera_height_cm": 11000.0,
    "camera_pitch_degrees": -90.0,
    "host_python": "auto",
    "reset_incompatible_work_cache": False,
}

GENERATED_CONNECTOR_ROUNDS = (
    {
        "max_distance_cm": 2000.0,
        "max_vertical_delta_cm": 200.0,
        "pairs_per_component_pair": 2,
        "max_candidates": 256,
    },
    {
        "max_distance_cm": 3000.0,
        "max_vertical_delta_cm": 300.0,
        "pairs_per_component_pair": 6,
        "max_candidates": 256,
    },
    {
        "max_distance_cm": 5000.0,
        "max_vertical_delta_cm": 500.0,
        "pairs_per_component_pair": 12,
        "max_candidates": 512,
    },
)

SEMANTIC_RECOVERY_MAX_ROUNDS = 24
SEMANTIC_GROUND_SEGMENT_MAX_CM = 500.0
SEMANTIC_LAYER_SEGMENT_MAX_CM = 250.0
SEMANTIC_SIDEWALK_OFFSETS_CM = (0.0, 20.0, -20.0)
SEMANTIC_SIDEWALK_EXPANSION_MAGNITUDES_CM = (50.0, 100.0, 200.0)


class CertificationError(RuntimeError):
    """A fail-closed certification or promotion error."""


class ProbeRejected(CertificationError):
    """A physical candidate failed one exclusive evidence stage."""

    def __init__(
        self,
        reason: str,
        detail: str,
        evidence: dict[str, Any] | None = None,
    ):
        if reason not in REJECTION_REASONS:
            raise ValueError("unknown rejection reason: {}".format(reason))
        super().__init__(detail)
        self.reason = reason
        self.detail = detail
        self.evidence = copy.deepcopy(evidence or {})


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def projected_source_sha256(value: Any) -> str:
    """Match project_central_source_with_cesium.py's newline hash contract."""

    return hashlib.sha256(canonical_json_bytes(value) + b"\n").hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    # OneDrive can restore a synced checkpoint with the Windows read-only bit
    # even though the directory remains writable. ``os.replace`` cannot replace
    # such a destination and retrying alone will never succeed. Clear only that
    # file attribute immediately before the atomic replacement; ACLs and every
    # other attribute remain untouched, and a later failure still stops the
    # certifier without promoting partial data.
    if path.exists() and not (path.stat().st_mode & stat.S_IWRITE):
        os.chmod(path, path.stat().st_mode | stat.S_IWRITE)
    # OneDrive/Defender can briefly hold the destination between the temp-file
    # flush and replace on Windows. A transient sharing violation must not
    # discard an otherwise valid, fail-closed checkpoint. Retry only the
    # replace operation; serialization remains deterministic and any final
    # failure still stops certification without promotion.
    retry_delays = (0.0, 0.025, 0.05, 0.1, 0.2, 0.4)
    for attempt, delay_seconds in enumerate(retry_delays):
        if delay_seconds:
            time.sleep(delay_seconds)
        try:
            os.replace(temporary, path)
            return
        except PermissionError:
            if attempt == len(retry_delays) - 1:
                raise


def stable_id(value: str) -> str:
    result = "".join(
        character.lower()
        if character.isascii() and (character.isalnum() or character in "._-")
        else "-"
        for character in str(value)
    )
    while "--" in result:
        result = result.replace("--", "-")
    result = result.strip("-.")
    if not result or not result[0].isalnum():
        result = "id-" + result
    if len(result) > 160:
        suffix = hashlib.sha256(result.encode("utf-8")).hexdigest()[:16]
        result = result[: 160 - len(suffix) - 1].rstrip("-.") + "-" + suffix
    return result


def vector_distance(first: Iterable[float], second: Iterable[float]) -> float:
    return math.sqrt(
        sum((float(a) - float(b)) ** 2 for a, b in zip(first, second))
    )


def distance_xy(first: Iterable[float], second: Iterable[float]) -> float:
    a = list(first)
    b = list(second)
    return math.hypot(float(a[0]) - float(b[0]), float(a[1]) - float(b[1]))


def lerp(first: float, second: float, alpha: float) -> float:
    return float(first) + (float(second) - float(first)) * float(alpha)


def round_vector(vector: Iterable[float]) -> list[float]:
    return [round(float(value), 4) for value in vector]


def station_distances(length_cm: float, spacing_cm: float) -> list[float]:
    if length_cm <= 0.0 or spacing_cm <= 0.0:
        raise CertificationError("station distance inputs must be positive")
    count = max(1, int(math.ceil(length_cm / spacing_cm)))
    return [length_cm * index / count for index in range(count + 1)]


def point_position(point: dict[str, Any]) -> list[float]:
    position = point.get("unreal_position_cm")
    if not isinstance(position, list) or len(position) != 3:
        raise CertificationError("projected source point has no Unreal position")
    return [float(value) for value in position]


def validate_inputs(
    projected: dict[str, Any], source: dict[str, Any], corrections: dict[str, Any]
) -> None:
    if projected.get("schema_version") != SCHEMA_VERSION:
        raise CertificationError("unsupported projected source schema")
    if source.get("schema_version") != SCHEMA_VERSION:
        raise CertificationError("unsupported source schema")
    if corrections.get("schema_version") != SCHEMA_VERSION:
        raise CertificationError("unsupported manual-correction schema")
    if len(projected.get("cells", [])) != CELL_COUNT:
        raise CertificationError("projected Central source must contain six cells")
    if len(projected.get("spawn_districts", [])) != CELL_COUNT:
        raise CertificationError("projected Central source must reserve six districts")
    topology = projected.get("hashes", {}).get("topology_sha256")
    if topology != source.get("hashes", {}).get("topology_sha256"):
        raise CertificationError("source/projected topology hash mismatch")
    if projected.get("network_id") != source.get("dataset_id"):
        raise CertificationError("source/projected network identity mismatch")
    if corrections.get("dataset_id") != source.get("dataset_id"):
        raise CertificationError("manual corrections target another dataset")


def accepted_manual_review_ids(corrections: dict[str, Any]) -> set[str]:
    accepted: set[str] = set()
    for entry in corrections.get("corrections", []):
        if not isinstance(entry, dict) or entry.get("review_status") != "accepted":
            continue
        action = str(entry.get("action", entry.get("operation", ""))).lower()
        feature_id = entry.get("feature_id")
        if action in {"accept", "include", "reclassify"} and isinstance(feature_id, str):
            accepted.add(feature_id)
    return accepted


def excluded_manual_review_ids(corrections: dict[str, Any]) -> set[str]:
    excluded: set[str] = set()
    for entry in corrections.get("corrections", []):
        if not isinstance(entry, dict) or entry.get("review_status") != "accepted":
            continue
        action = str(entry.get("action", entry.get("operation", ""))).lower()
        feature_id = entry.get("feature_id")
        if action == "exclude" and isinstance(feature_id, str):
            excluded.add(feature_id)
    return excluded


def build_source_plan(
    projected: dict[str, Any], corrections: dict[str, Any]
) -> dict[str, Any]:
    """Select the largest real OSM component, never review-gated geometry."""

    accepted_reviews = accepted_manual_review_ids(corrections)
    explicit_exclusions = excluded_manual_review_ids(corrections)
    admitted_features: list[dict[str, Any]] = []
    review_blocked: list[str] = []
    explicitly_excluded: list[str] = []
    for feature in sorted(projected["features"], key=lambda item: item["feature_id"]):
        feature_id = feature["feature_id"]
        if feature_id in explicit_exclusions:
            explicitly_excluded.append(feature_id)
            continue
        if feature.get("requires_manual_review", False) and feature_id not in accepted_reviews:
            review_blocked.append(feature_id)
            continue
        if len(feature.get("points", [])) >= 2:
            admitted_features.append(feature)

    raw_candidates: list[dict[str, Any]] = []
    point_cells: dict[str, set[str]] = defaultdict(set)
    point_records: dict[str, dict[str, Any]] = {}
    for feature in admitted_features:
        points = feature["points"]
        for point in points:
            point_id = stable_id(point["point_id"])
            point_cells[point_id].add(feature["cell_id"])
            point_records.setdefault(point_id, point)
        for index, (first, second) in enumerate(zip(points, points[1:])):
            first_position = point_position(first)
            second_position = point_position(second)
            length_cm = distance_xy(first_position, second_position)
            if length_cm <= 1.0:
                continue
            raw_candidates.append(
                {
                    "candidate_id": stable_id(
                        "candidate-{}-s{:03d}".format(feature["feature_id"], index)
                    ),
                    "source_feature_id": stable_id(feature["feature_id"]),
                    "source_cell_id": stable_id(feature["cell_id"]),
                    "classification": feature["classification"],
                    "topology_origin": "openstreetmap",
                    "from_point_id": stable_id(first["point_id"]),
                    "to_point_id": stable_id(second["point_id"]),
                    "from_source_position": first_position,
                    "to_source_position": second_position,
                    "source_length_xy_cm": length_cm,
                    "tags": feature.get("tags", {}),
                    "requires_manual_review": bool(
                        feature.get("requires_manual_review", False)
                    ),
                }
            )

    adjacency: dict[str, set[str]] = defaultdict(set)
    edges_for_node: dict[str, list[int]] = defaultdict(list)
    for index, candidate in enumerate(raw_candidates):
        first = candidate["from_point_id"]
        second = candidate["to_point_id"]
        adjacency[first].add(second)
        adjacency[second].add(first)
        edges_for_node[first].append(index)
        edges_for_node[second].append(index)

    remaining = set(adjacency)
    components: list[dict[str, Any]] = []
    while remaining:
        start = min(remaining)
        queue = deque([start])
        nodes = {start}
        remaining.remove(start)
        while queue:
            node = queue.popleft()
            for neighbor in adjacency[node]:
                if neighbor in remaining:
                    remaining.remove(neighbor)
                    nodes.add(neighbor)
                    queue.append(neighbor)
        edge_indices = sorted(
            {
                edge_index
                for node in nodes
                for edge_index in edges_for_node[node]
                if raw_candidates[edge_index]["from_point_id"] in nodes
                and raw_candidates[edge_index]["to_point_id"] in nodes
            }
        )
        cell_ids = sorted(
            {raw_candidates[index]["source_cell_id"] for index in edge_indices}
        )
        components.append(
            {
                "node_ids": sorted(nodes),
                "edge_indices": edge_indices,
                "cell_ids": cell_ids,
                "length_cm": sum(
                    raw_candidates[index]["source_length_xy_cm"]
                    for index in edge_indices
                ),
            }
        )

    components.sort(
        key=lambda item: (
            -len(item["cell_ids"]),
            -item["length_cm"],
            -len(item["node_ids"]),
            item["node_ids"],
        )
    )
    if not components:
        raise CertificationError("no admitted OSM pedestrian segments")
    primary = components[0]
    selected = [raw_candidates[index] for index in primary["edge_indices"]]
    selected.sort(key=lambda item: item["candidate_id"])

    # Node ownership is deterministic and independent of trace order.  A
    # shared boundary point has one owner; a segment crossing ownership cells
    # becomes a paired portal lane during final assembly.
    selected_node_ids = set(primary["node_ids"])
    cell_bounds = {
        cell["cell_id"]: cell["world_bounds_cm"] for cell in projected["cells"]
    }
    node_owner = {}
    for point_id in selected_node_ids:
        position = point_position(point_records[point_id])
        containing_cells = sorted(
            cell_id
            for cell_id, bounds in cell_bounds.items()
            if float(bounds["min"][0]) - 5.0
            <= position[0]
            <= float(bounds["max"][0]) + 5.0
            and float(bounds["min"][1]) - 5.0
            <= position[1]
            <= float(bounds["max"][1]) + 5.0
        )
        if not containing_cells:
            containing_cells = sorted(point_cells[point_id])
        if not containing_cells:
            raise CertificationError(
                "source point {} has no owning Central cell".format(point_id)
            )
        node_owner[point_id] = containing_cells[0]
    for candidate in selected:
        candidate["from_cell_id"] = node_owner[candidate["from_point_id"]]
        candidate["to_cell_id"] = node_owner[candidate["to_point_id"]]

    # A breadth-first edge order propagates a collision-grounded elevation
    # seed through the connected OSM graph instead of independently shooting
    # sky rays that could mistake a roof for the intended pavement.
    ordered: list[dict[str, Any]] = []
    used_edges: set[str] = set()
    visited_nodes: set[str] = set()
    by_node: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for candidate in selected:
        by_node[candidate["from_point_id"]].append(candidate)
        by_node[candidate["to_point_id"]].append(candidate)
    selected_positions = [
        point_position(point_records[node_id]) for node_id in selected_node_ids
    ]
    network_center = [
        (min(position[axis] for position in selected_positions)
         + max(position[axis] for position in selected_positions))
        * 0.5
        for axis in range(2)
    ]
    root_node = min(
        selected_node_ids,
        key=lambda node_id: (
            distance_xy(point_position(point_records[node_id]), network_center),
            abs(point_position(point_records[node_id])[2]),
            node_id,
        ),
    )
    queue = deque([root_node])
    visited_nodes.add(root_node)
    while queue:
        node_id = queue.popleft()
        for original in sorted(by_node[node_id], key=lambda item: item["candidate_id"]):
            if original["candidate_id"] in used_edges:
                continue
            candidate = copy.deepcopy(original)
            if candidate["to_point_id"] == node_id:
                candidate = reverse_candidate(candidate)
            used_edges.add(candidate["candidate_id"])
            ordered.append(candidate)
            other = candidate["to_point_id"]
            if other not in visited_nodes:
                visited_nodes.add(other)
                queue.append(other)
    if len(ordered) != len(selected):
        raise CertificationError("primary source component traversal was incomplete")

    # Keep the BFS-derived order inside each cell, but visit each cell only
    # once.  Repeated viewport hopping would continuously evict/reload Cesium
    # tiles and make a whole-area certificate both slow and non-reproducible.
    cell_visit_order = list(dict.fromkeys(item["source_cell_id"] for item in ordered))
    ordered = [
        item
        for cell_id in cell_visit_order
        for item in ordered
        if item["source_cell_id"] == cell_id
    ]

    return {
        "network_id": projected["network_id"],
        "projected_hashes": projected["hashes"],
        "cell_ids": sorted(cell["cell_id"] for cell in projected["cells"]),
        "cell_records": copy.deepcopy(projected["cells"]),
        "spawn_districts": copy.deepcopy(projected["spawn_districts"]),
        "candidates": ordered,
        "node_owner": node_owner,
        "point_records": {
            point_id: point_records[point_id] for point_id in selected_node_ids
        },
        "review_blocked_feature_ids": review_blocked,
        "explicitly_excluded_feature_ids": explicitly_excluded,
        "accepted_review_feature_ids": sorted(accepted_reviews),
        "source_component_count": len(components),
        "primary_component": {
            "cell_ids": primary["cell_ids"],
            "node_count": len(primary["node_ids"]),
            "undirected_candidate_count": len(selected),
            "length_cm": round(primary["length_cm"], 3),
        },
        "cell_visit_order": cell_visit_order,
        "excluded_component_summaries": [
            {
                "cell_ids": component["cell_ids"],
                "node_count": len(component["node_ids"]),
                "undirected_candidate_count": len(component["edge_indices"]),
                "length_cm": round(component["length_cm"], 3),
            }
            for component in components[1:]
        ],
    }


def reverse_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    reversed_candidate = copy.deepcopy(candidate)
    for suffix in ("point_id", "source_position", "cell_id"):
        first_key = "from_" + suffix
        second_key = "to_" + suffix
        reversed_candidate[first_key], reversed_candidate[second_key] = (
            reversed_candidate[second_key],
            reversed_candidate[first_key],
        )
    reversed_candidate["processing_reversed"] = not bool(
        candidate.get("processing_reversed", False)
    )
    return reversed_candidate


def classification_name(value: str) -> str:
    mapping = {
        "sidewalk": "sidewalk",
        "footway": "footway",
        "crossing": "crossing",
        "pedestrian-zone": "pedestrian-zone",
        "pedestrian": "pedestrian-zone",
        "manual-pedestrian-link": "manual-pedestrian-link",
    }
    if value not in mapping:
        raise CertificationError("unsupported pedestrian class: {}".format(value))
    return mapping[value]


def lane_width(candidate: dict[str, Any], settings: dict[str, Any]) -> float:
    classification = classification_name(candidate["classification"])
    if classification in {"crossing", "pedestrian-zone"}:
        return 160.0
    return float(settings["lane_width_cm"])


def create_work_document(
    plan: dict[str, Any],
    projected_path: Path,
    corrections_path: Path,
    settings: dict[str, Any],
    runtime_hashes: dict[str, str],
) -> dict[str, Any]:
    compatibility = {
        "projected_document_sha256": file_sha256(projected_path),
        "projected_combined_sha256": plan["projected_hashes"]["combined_sha256"],
        "manual_corrections_sha256": file_sha256(corrections_path),
        "settings_sha256": sha256_json(settings),
        "georeference_sha256": runtime_hashes["georeference_sha256"],
        "tileset_sha256": runtime_hashes["tileset_sha256"],
        "collision_settings_sha256": runtime_hashes["collision_settings_sha256"],
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "generator_version": GENERATOR_VERSION,
        "network_id": plan["network_id"],
        "compatibility": compatibility,
        "started_at_utc": utc_timestamp(),
        "updated_at_utc": utc_timestamp(),
        "complete": False,
        "results": {},
        "streaming_cells": {},
    }


def validate_or_create_work(
    plan: dict[str, Any],
    projected_path: Path,
    corrections_path: Path,
    settings: dict[str, Any],
    runtime_hashes: dict[str, str],
    work_path: Path = WORK_PATH,
) -> dict[str, Any]:
    expected = create_work_document(
        plan, projected_path, corrections_path, settings, runtime_hashes
    )
    if not work_path.exists():
        write_json_atomic(work_path, expected)
        return expected
    current = read_json(work_path)
    if current.get("compatibility") != expected["compatibility"]:
        if settings.get("reset_incompatible_work_cache", False):
            stale = work_path.with_name(
                work_path.name + ".incompatible-" + time.strftime("%Y%m%dT%H%M%S")
            )
            os.replace(work_path, stale)
            write_json_atomic(work_path, expected)
            return expected
        raise CertificationError(
            "working cache compatibility changed; preserve/inspect it or set "
            "reset_incompatible_work_cache=true explicitly"
        )
    return current


def utc_timestamp() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def hit_property(hit: Any, name: str, default: Any = None) -> Any:
    if hit is None:
        return default
    try:
        return hit.get_editor_property(name)
    except Exception:
        pass
    try:
        return getattr(hit, name)
    except Exception:
        return default


def object_path(value: Any) -> str:
    if value is None:
        return ""
    try:
        return str(value.get_path_name())
    except Exception:
        return str(value)


class CesiumProbeAdapter:
    """Strict world/direct collision queries used only inside Unreal."""

    def __init__(self, world: Any, settings: dict[str, Any]):
        if unreal is None:  # pragma: no cover - Unreal-only guard
            raise CertificationError("CesiumProbeAdapter requires Unreal")
        self.world = world
        self.settings = settings
        # Capture the persistent tileset actors while the MCP command is on the
        # editor thread. Re-enumerating all level actors from every Slate
        # post-tick callback can transiently return an empty list even though
        # the already loaded Cesium primitives remain queryable.
        self.tileset_actors = [
            actor
            for actor in unreal.EditorLevelLibrary.get_all_level_actors()
            if "Cesium3DTileset" in actor.get_class().get_name()
        ]
        if not self.tileset_actors:
            raise CertificationError("no Cesium3DTileset actor is loaded")
        self.components: list[dict[str, Any]] = []
        self.signature_component_count = 0
        self.refresh_components()

    def refresh_components(
        self, cell_bounds: dict[str, Any] | None = None
    ) -> str:
        records = []
        for actor in self.tileset_actors:
            for component in actor.get_components_by_class(unreal.PrimitiveComponent):
                if component.get_class().get_name() != "CesiumGltfPrimitiveComponent":
                    continue
                try:
                    accepted = (
                        component.is_visible()
                        and component.is_query_collision_enabled()
                    )
                except Exception:
                    accepted = False
                if not accepted:
                    continue
                origin, extent, _radius = unreal.SystemLibrary.get_component_bounds(component)
                records.append(
                    {
                        "component": component,
                        "path": object_path(component),
                        "min": [
                            float(origin.x - extent.x),
                            float(origin.y - extent.y),
                            float(origin.z - extent.z),
                        ],
                        "max": [
                            float(origin.x + extent.x),
                            float(origin.y + extent.y),
                            float(origin.z + extent.z),
                        ],
                    }
                )
        records.sort(key=lambda record: record["path"])
        self.components = records
        signature_records = records
        if cell_bounds is not None:
            padding = float(self.settings["component_bounds_padding_cm"])
            cell_min = cell_bounds["min"]
            cell_max = cell_bounds["max"]
            signature_records = [
                record
                for record in records
                if record["max"][0] + padding >= float(cell_min[0])
                and record["min"][0] - padding <= float(cell_max[0])
                and record["max"][1] + padding >= float(cell_min[1])
                and record["min"][1] - padding <= float(cell_max[1])
            ]
        self.signature_component_count = len(signature_records)
        return sha256_json(
            [
                {
                    "path": record["path"],
                    "min": round_vector(record["min"]),
                    "max": round_vector(record["max"]),
                }
                for record in signature_records
            ]
        )

    def relevant_components(self, x: float, y: float) -> list[dict[str, Any]]:
        padding = float(self.settings["component_bounds_padding_cm"])
        return [
            record
            for record in self.components
            if record["min"][0] - padding <= x <= record["max"][0] + padding
            and record["min"][1] - padding <= y <= record["max"][1] + padding
        ]

    def _world_hit(self, start: Any, end: Any) -> dict[str, Any] | None:
        hit = unreal.SystemLibrary.line_trace_single_for_objects(
            self.world,
            start,
            end,
            [unreal.ObjectTypeQuery.OBJECT_TYPE_QUERY1],
            True,
            [],
            unreal.DrawDebugTrace.NONE,
            True,
        )
        if not hit_property(hit, "blocking_hit", False):
            return None
        point = hit_property(hit, "impact_point") or hit_property(hit, "location")
        normal = hit_property(hit, "impact_normal") or hit_property(hit, "normal")
        component = hit_property(hit, "component") or hit_property(hit, "hit_component")
        if point is None or normal is None:
            return None
        return {"point": point, "normal": normal, "component": component}

    def trace_support(
        self, x: float, y: float, expected_z: float, *, seed: bool = False
    ) -> dict[str, Any]:
        start = unreal.Vector(
            x, y, expected_z + float(self.settings["trace_height_cm"])
        )
        end = unreal.Vector(x, y, expected_z - float(self.settings["trace_depth_cm"]))
        # Cesium editor primitives expose their real physics mesh through
        # line_trace_component, but are not inserted into the editor world's
        # broadphase (world/object traces return no hit despite
        # QUERY_AND_PHYSICS). Compare every XY-relevant Cesium component and
        # retain the nearest raw blocker so the result is still fail-closed.
        nearest = None
        nearest_distance = math.inf
        for record in self.relevant_components(x, y):
            try:
                hit = record["component"].line_trace_component(
                    start, end, True, False, False
                )
            except Exception:
                continue
            if not hit:
                continue
            point, normal, _bone_name, _hit_result = hit
            distance = vector_distance(
                [start.x, start.y, start.z], [point.x, point.y, point.z]
            )
            if distance < nearest_distance:
                nearest = {"point": point, "normal": normal, "component": record["component"]}
                nearest_distance = distance

        if nearest is None:
            raise ProbeRejected(
                "missing_support",
                "no raw collision blocker at exact XY",
                {
                    "query_xy": round_vector([x, y]),
                    "expected_z_cm": round(float(expected_z), 4),
                    "seed": bool(seed),
                },
            )
        component = nearest["component"]
        if component is None or component.get_class().get_name() != "CesiumGltfPrimitiveComponent":
            raise ProbeRejected(
                "first_blocker", "global raw first blocker is not Cesium geometry"
            )
        normal = nearest["normal"]
        if float(normal.z) < float(self.settings["min_walkable_normal_z"]):
            raise ProbeRejected(
                "slope",
                "Cesium first blocker is not walkable",
                {
                    "query_xy": round_vector([x, y]),
                    "expected_z_cm": round(float(expected_z), 4),
                    "hit_z_cm": round(float(nearest["point"].z), 4),
                    "surface_normal": round_vector(
                        [float(normal.x), float(normal.y), float(normal.z)]
                    ),
                    "seed": bool(seed),
                },
            )
        tolerance = float(
            self.settings[
                "seed_height_tolerance_cm" if seed else "ground_height_tolerance_cm"
            ]
        )
        point = nearest["point"]
        if abs(float(point.z) - expected_z) > tolerance:
            raise ProbeRejected(
                "height_continuity",
                "first blocker differs from expected elevation by {:.3f} cm".format(
                    abs(float(point.z) - expected_z)
                ),
                {
                    "query_xy": round_vector([x, y]),
                    "expected_z_cm": round(float(expected_z), 4),
                    "hit_z_cm": round(float(point.z), 4),
                    "height_delta_cm": round(abs(float(point.z) - expected_z), 4),
                    "tolerance_cm": round(tolerance, 4),
                    "seed": bool(seed),
                },
            )

        size = math.sqrt(float(normal.x) ** 2 + float(normal.y) ** 2 + float(normal.z) ** 2)
        if size <= 1.0e-6:
            raise ProbeRejected("slope", "zero-length surface normal")
        return {
            "ground_position": [float(point.x), float(point.y), float(point.z)],
            "surface_normal": [
                float(normal.x) / size,
                float(normal.y) / size,
                float(normal.z) / size,
            ],
            "supporting_primitive_id": stable_id(object_path(component)),
        }

    def capsule_clear(self, start: list[float], end: list[float]) -> bool:
        clearance = float(self.settings["pedestrian_clearance_above_ground_cm"])
        full_height = float(self.settings["pedestrian_half_height_cm"]) * 2.0
        # Adjacent strict support tracks/stations are at most 10 cm apart.
        # Probe both ends and the midpoint vertically through the pedestrian
        # volume against every relevant real Cesium mesh. Across the dense
        # longitudinal and transverse grid this forms a conservative swept
        # clearance lattice without relying on the editor-world broadphase.
        for alpha in (0.0, 0.5, 1.0):
            x = lerp(float(start[0]), float(end[0]), alpha)
            y = lerp(float(start[1]), float(end[1]), alpha)
            ground_z = lerp(float(start[2]), float(end[2]), alpha)
            begin = unreal.Vector(x, y, ground_z + clearance)
            finish = unreal.Vector(x, y, ground_z + clearance + full_height)
            for record in self.relevant_components(x, y):
                try:
                    hit = record["component"].line_trace_component(
                        begin, finish, True, False, False
                    )
                except Exception:
                    return False
                if hit:
                    return False
        return True


def continuity_check(
    previous: list[float],
    current: list[float],
    settings: dict[str, Any],
    *,
    expected_spacing_cm: float,
) -> None:
    horizontal = distance_xy(previous, current)
    dz = abs(float(current[2]) - float(previous[2]))
    if horizontal > expected_spacing_cm + 0.25:
        raise ProbeRejected(
            "height_continuity",
            "support sample spacing exceeded its certificate",
            {
                "previous_position": round_vector(previous),
                "current_position": round_vector(current),
                "horizontal_distance_cm": round(horizontal, 4),
                "expected_spacing_cm": round(float(expected_spacing_cm), 4),
            },
        )
    if dz > float(settings["max_neighbor_height_delta_cm"]):
        raise ProbeRejected(
            "height_continuity",
            "neighbor height delta exceeded 55 cm",
            {
                "previous_position": round_vector(previous),
                "current_position": round_vector(current),
                "horizontal_distance_cm": round(horizontal, 4),
                "height_delta_cm": round(dz, 4),
            },
        )
    if horizontal <= 1.0e-6 or dz / horizontal > float(settings["max_grade"]):
        raise ProbeRejected(
            "slope",
            "point-to-point grade exceeded the walkable limit",
            {
                "previous_position": round_vector(previous),
                "current_position": round_vector(current),
                "horizontal_distance_cm": round(horizontal, 4),
                "height_delta_cm": round(dz, 4),
                "grade": None if horizontal <= 1.0e-6 else round(dz / horizontal, 6),
            },
        )


def interpolate_candidate(candidate: dict[str, Any], distance_cm: float) -> list[float]:
    length = float(candidate["source_length_xy_cm"])
    alpha = 0.0 if length <= 0.0 else distance_cm / length
    first = candidate["from_source_position"]
    second = candidate["to_source_position"]
    return [lerp(first[index], second[index], alpha) for index in range(3)]


def certify_candidate_steps(
    candidate: dict[str, Any],
    adapter: Any,
    settings: dict[str, Any],
    known_nodes: dict[str, list[float]],
) -> Iterator[dict[str, Any]]:
    """Yield after each expensive query; return one accepted/rejected result."""

    candidate_id = candidate["candidate_id"]
    length_xy = float(candidate["source_length_xy_cm"])
    width_cm = lane_width(candidate, settings)
    side_offset = width_cm * 0.5 - float(settings["pedestrian_radius_cm"])
    if side_offset <= 1.0:
        return {
            "status": "rejected",
            "candidate_id": candidate_id,
            "reason": "multi_track",
            "detail": "lane width leaves no distinct side tracks",
        }
    direction_x = (
        float(candidate["to_source_position"][0])
        - float(candidate["from_source_position"][0])
    ) / length_xy
    direction_y = (
        float(candidate["to_source_position"][1])
        - float(candidate["from_source_position"][1])
    ) / length_xy
    right = [-direction_y, direction_x]
    failure_context: dict[str, Any] = {}

    def allow_unknown_semantic_seed() -> bool:
        if candidate.get("topology_origin") != "osm-semantic-recovery":
            return True
        return (
            candidate.get("semantic_seed_policy") == "explicit-layer-once"
            and semantic_profile(candidate)["seed_eligible"]
        )

    try:
        coarse_previous = None
        coarse_expected = None
        for index, distance in enumerate(
            station_distances(length_xy, float(settings["coarse_spacing_cm"]))
        ):
            source_position = interpolate_candidate(candidate, distance)
            failure_context = {
                "phase": "coarse-support",
                "station_distance_cm": round(float(distance), 4),
                "source_position": round_vector(source_position),
            }
            if index == 0:
                known = known_nodes.get(candidate["from_point_id"])
                if known is None and not allow_unknown_semantic_seed():
                    raise ProbeRejected(
                        "height_continuity",
                        "semantic recovery requires a certified predecessor",
                        {"semantic_seed_policy": candidate.get("semantic_seed_policy")},
                    )
                coarse_expected = (
                    float(known[2]) - float(settings["lane_height_offset_cm"])
                    if known is not None
                    else float(source_position[2])
                )
                seed = known is None
            else:
                coarse_expected = float(coarse_previous[2])
                seed = False
            hit = adapter.trace_support(
                source_position[0], source_position[1], coarse_expected, seed=seed
            )
            yield {"phase": "coarse", "candidate_id": candidate_id}
            current = hit["ground_position"]
            if coarse_previous is not None:
                horizontal = distance_xy(coarse_previous, current)
                dz = abs(float(current[2]) - float(coarse_previous[2]))
                if horizontal <= 1.0e-6 or dz / horizontal > float(settings["max_grade"]):
                    raise ProbeRejected("slope", "coarse support grade failed")
            coarse_previous = current

        offsets = [0.0]
        step = float(settings["support_spacing_cm"])
        cross_count = max(1, int(math.ceil(side_offset / step)))
        for cross_index in range(1, cross_count + 1):
            offset = side_offset * cross_index / cross_count
            offsets.extend([-offset, offset])
        offsets = sorted(set(round(value, 6) for value in offsets))
        primary_offsets = (-side_offset, 0.0, side_offset)
        previous_tracks: dict[float, list[float]] = {}
        previous_expected: dict[float, float] = {}
        output_samples: list[dict[str, Any]] = []
        strict_distances = station_distances(
            length_xy, float(settings["support_spacing_cm"])
        )
        for sample_index, distance in enumerate(strict_distances):
            source_position = interpolate_candidate(candidate, distance)
            track_hits: dict[float, dict[str, Any]] = {}
            # Center first, then work outwards so transverse expected Z is
            # grounded rather than the ellipsoid-height source placeholder.
            ordered_offsets = sorted(offsets, key=lambda value: (abs(value), value))
            for offset in ordered_offsets:
                x = source_position[0] + right[0] * offset
                y = source_position[1] + right[1] * offset
                failure_context = {
                    "phase": "strict-support",
                    "station_distance_cm": round(float(distance), 4),
                    "track_offset_cm": round(float(offset), 4),
                    "source_position": round_vector(source_position),
                    "query_xy": round_vector([x, y]),
                }
                if offset in previous_expected:
                    expected_z = previous_expected[offset]
                    seed = False
                elif sample_index == 0 and candidate["from_point_id"] in known_nodes:
                    expected_z = (
                        known_nodes[candidate["from_point_id"]][2]
                        - float(settings["lane_height_offset_cm"])
                    )
                    seed = False
                elif 0.0 in track_hits:
                    expected_z = track_hits[0.0]["ground_position"][2]
                    seed = False
                else:
                    if sample_index == 0 and not allow_unknown_semantic_seed():
                        raise ProbeRejected(
                            "height_continuity",
                            "semantic recovery requires a certified predecessor",
                            {
                                "semantic_seed_policy": candidate.get(
                                    "semantic_seed_policy"
                                )
                            },
                        )
                    expected_z = source_position[2]
                    seed = True
                hit = adapter.trace_support(x, y, expected_z, seed=seed)
                yield {"phase": "strict-support", "candidate_id": candidate_id}
                track_hits[offset] = hit

            sorted_offsets = sorted(offsets)
            for first_offset, second_offset in zip(sorted_offsets, sorted_offsets[1:]):
                failure_context = {
                    "phase": "strict-cross-continuity",
                    "station_distance_cm": round(float(distance), 4),
                    "track_offsets_cm": [first_offset, second_offset],
                    "source_position": round_vector(source_position),
                }
                try:
                    continuity_check(
                        track_hits[first_offset]["ground_position"],
                        track_hits[second_offset]["ground_position"],
                        settings,
                        expected_spacing_cm=float(settings["support_spacing_cm"]),
                    )
                except ProbeRejected as error:
                    raise ProbeRejected(
                        "multi_track", error.detail, error.evidence
                    ) from error
                if not adapter.capsule_clear(
                    track_hits[first_offset]["ground_position"],
                    track_hits[second_offset]["ground_position"],
                ):
                    raise ProbeRejected(
                        "capsule_clearance", "transverse pedestrian capsule was blocked"
                    )
                yield {"phase": "strict-cross-clearance", "candidate_id": candidate_id}

            if previous_tracks:
                for primary in primary_offsets:
                    failure_context = {
                        "phase": "strict-longitudinal-continuity",
                        "station_distance_cm": round(float(distance), 4),
                        "track_offset_cm": round(float(primary), 4),
                        "source_position": round_vector(source_position),
                    }
                    continuity_check(
                        previous_tracks[primary],
                        track_hits[primary]["ground_position"],
                        settings,
                        expected_spacing_cm=float(settings["support_spacing_cm"]),
                    )
                    if not adapter.capsule_clear(
                        previous_tracks[primary], track_hits[primary]["ground_position"]
                    ):
                        raise ProbeRejected(
                            "capsule_clearance",
                            "longitudinal pedestrian capsule was blocked",
                        )
                    yield {
                        "phase": "strict-longitudinal-clearance",
                        "candidate_id": candidate_id,
                    }

            center = track_hits[0.0]
            left = track_hits[-side_offset]
            right_hit = track_hits[side_offset]
            lane_offset = float(settings["lane_height_offset_cm"])
            output_samples.append(
                {
                    "center_position": round_vector(
                        [
                            center["ground_position"][0],
                            center["ground_position"][1],
                            center["ground_position"][2] + lane_offset,
                        ]
                    ),
                    "left_track_position": round_vector(
                        [
                            left["ground_position"][0],
                            left["ground_position"][1],
                            left["ground_position"][2] + lane_offset,
                        ]
                    ),
                    "right_track_position": round_vector(
                        [
                            right_hit["ground_position"][0],
                            right_hit["ground_position"][1],
                            right_hit["ground_position"][2] + lane_offset,
                        ]
                    ),
                    "surface_normal": round_vector(center["surface_normal"]),
                    "supporting_primitive_id": center["supporting_primitive_id"],
                    "evidence_mask": FULL_GROUND_EVIDENCE_MASK,
                }
            )
            previous_tracks = {
                offset: track_hits[offset]["ground_position"] for offset in offsets
            }
            previous_expected = {
                offset: track_hits[offset]["ground_position"][2] for offset in offsets
            }

        expected_end = known_nodes.get(candidate["to_point_id"])
        if expected_end is not None and vector_distance(
            expected_end, output_samples[-1]["center_position"]
        ) > 5.0:
            raise ProbeRejected(
                "height_continuity", "shared endpoint disagrees with its certified node"
            )
        return {
            "status": "accepted",
            "candidate_id": candidate_id,
            "source_feature_id": candidate["source_feature_id"],
            "classification": candidate["classification"],
            "topology_origin": candidate["topology_origin"],
            "from_point_id": candidate["from_point_id"],
            "to_point_id": candidate["to_point_id"],
            "width_cm": width_cm,
            "coarse_station_count": len(
                station_distances(length_xy, float(settings["coarse_spacing_cm"]))
            ),
            "samples": output_samples,
        }
    except ProbeRejected as error:
        evidence = copy.deepcopy(failure_context)
        evidence.update(error.evidence)
        return {
            "status": "rejected",
            "candidate_id": candidate_id,
            "reason": error.reason,
            "detail": error.detail,
            "failure_evidence": evidence,
        }


def exhaust_generator(generator: Iterator[dict[str, Any]]) -> dict[str, Any]:
    while True:
        try:
            next(generator)
        except StopIteration as finished:
            return finished.value


def rebuild_known_nodes(
    results: dict[str, Any],
    *,
    allowed_origins: frozenset[str] | set[str] | None = None,
) -> dict[str, list[float]]:
    known: dict[str, list[float]] = {}
    for result in results.values():
        if result.get("status") != "accepted" or not result.get("samples"):
            continue
        if (
            allowed_origins is not None
            and result.get("topology_origin") not in allowed_origins
        ):
            continue
        for point_id, position in (
            (result["from_point_id"], result["samples"][0]["center_position"]),
            (result["to_point_id"], result["samples"][-1]["center_position"]),
        ):
            previous = known.get(point_id)
            if previous is not None and vector_distance(previous, position) > 5.0:
                raise CertificationError(
                    "working cache contains inconsistent shared endpoint {}".format(point_id)
                )
            known.setdefault(point_id, position)
    return known


def accepted_components(
    plan: dict[str, Any],
    results: dict[str, Any],
    *,
    allowed_origins: frozenset[str] | set[str] | None = None,
    allowed_candidate_ids: set[str] | frozenset[str] | None = None,
) -> list[dict[str, Any]]:
    candidate_by_id = {
        candidate["candidate_id"]: candidate for candidate in plan["candidates"]
    }
    accepted = [
        (candidate_by_id[candidate_id], result)
        for candidate_id, result in results.items()
        if result.get("status") == "accepted"
        and candidate_id in candidate_by_id
        and (
            allowed_origins is None
            or candidate_by_id[candidate_id].get("topology_origin")
            in allowed_origins
        )
        and (
            allowed_candidate_ids is None
            or candidate_id in allowed_candidate_ids
        )
    ]
    adjacency: dict[str, set[str]] = defaultdict(set)
    by_node: dict[str, list[tuple[dict[str, Any], dict[str, Any]]]] = defaultdict(list)
    for candidate, result in accepted:
        first = candidate["from_point_id"]
        second = candidate["to_point_id"]
        adjacency[first].add(second)
        adjacency[second].add(first)
        by_node[first].append((candidate, result))
        by_node[second].append((candidate, result))
    remaining = set(adjacency)
    components = []
    while remaining:
        start = min(remaining)
        remaining.remove(start)
        nodes = {start}
        queue = deque([start])
        while queue:
            node = queue.popleft()
            for neighbor in adjacency[node]:
                if neighbor in remaining:
                    remaining.remove(neighbor)
                    nodes.add(neighbor)
                    queue.append(neighbor)
        pairs_by_id = {}
        for node in nodes:
            for candidate, result in by_node[node]:
                if (
                    candidate["from_point_id"] in nodes
                    and candidate["to_point_id"] in nodes
                ):
                    pairs_by_id[candidate["candidate_id"]] = (candidate, result)
        pairs = [pairs_by_id[key] for key in sorted(pairs_by_id)]
        cells = sorted(
            {
                plan["node_owner"][node]
                for node in nodes
                if node in plan["node_owner"]
            }
        )
        components.append(
            {
                "nodes": nodes,
                "pairs": pairs,
                "cells": cells,
                "length_cm": sum(
                    polyline_length(result["samples"]) for _candidate, result in pairs
                ),
            }
        )
    components.sort(
        key=lambda component: (
            -len(component["cells"]),
            -component["length_cm"],
            -len(component["nodes"]),
            sorted(component["nodes"]),
        )
    )
    return components


def trusted_runtime_result_ids(
    plan: dict[str, Any], results: dict[str, Any]
) -> set[str]:
    """Select every independently certified real OSM/semantic subsegment.

    Split semantic stations have distinct node identifiers, so an accepted
    subsegment cannot bridge a rejected sibling. Generated/manual connectors
    remain excluded unless their complete Cesium result also carries an
    explicit accepted visual-review decision.
    """

    candidate_by_id = {
        candidate["candidate_id"]: candidate for candidate in plan["candidates"]
    }
    return {
        candidate_id
        for candidate_id, result in results.items()
        if result.get("status") == "accepted"
        and candidate_id in candidate_by_id
        and (
            candidate_by_id[candidate_id].get("topology_origin")
            in TRUSTED_RUNTIME_TOPOLOGY_ORIGINS
            or (
                candidate_by_id[candidate_id].get("topology_origin")
                in REVIEWABLE_RUNTIME_TOPOLOGY_ORIGINS
                and candidate_by_id[candidate_id].get("requires_manual_review")
                is True
                and candidate_by_id[candidate_id].get("manual_review_status")
                == "accepted"
            )
        )
    }


def polyline_length(samples: list[dict[str, Any]]) -> float:
    return sum(
        vector_distance(first["center_position"], second["center_position"])
        for first, second in zip(samples, samples[1:])
    )


def orient_result_to_candidate(
    candidate: dict[str, Any], result: dict[str, Any]
) -> list[dict[str, Any]]:
    samples = copy.deepcopy(result["samples"])
    if (
        result["from_point_id"] == candidate["from_point_id"]
        and result["to_point_id"] == candidate["to_point_id"]
    ):
        return samples
    if (
        result["from_point_id"] == candidate["to_point_id"]
        and result["to_point_id"] == candidate["from_point_id"]
    ):
        return reverse_sample_geometry(samples)
    raise CertificationError("candidate result endpoints changed identity")


def reverse_sample_geometry(samples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    reversed_samples = []
    for sample in reversed(samples):
        item = copy.deepcopy(sample)
        item["left_track_position"], item["right_track_position"] = (
            item["right_track_position"],
            item["left_track_position"],
        )
        reversed_samples.append(item)
    return reversed_samples


def canonical_node_positions(
    pairs: list[tuple[dict[str, Any], dict[str, Any]]]
) -> dict[str, list[float]]:
    observations: dict[str, list[list[float]]] = defaultdict(list)
    for candidate, result in pairs:
        samples = orient_result_to_candidate(candidate, result)
        observations[candidate["from_point_id"]].append(samples[0]["center_position"])
        observations[candidate["to_point_id"]].append(samples[-1]["center_position"])
    positions = {}
    for node_id, values in observations.items():
        reference = values[0]
        if any(vector_distance(reference, value) > 5.0 for value in values[1:]):
            raise CertificationError(
                "certified shared endpoint {} differs by more than 5 cm".format(node_id)
            )
        positions[node_id] = round_vector(
            [sum(value[axis] for value in values) / len(values) for axis in range(3)]
        )
    return positions


def semantic_profile(candidate: dict[str, Any]) -> dict[str, Any]:
    """Return deterministic OSM level/structure semantics for recovery."""

    tags = candidate.get("tags", {})
    raw_layer = str(tags.get("layer", "0")).strip()
    layer = "0" if raw_layer in {"", "0.0", "None", "none"} else raw_layer
    highway = str(tags.get("highway", "")).lower()
    transition = (
        highway in {"steps", "elevator"}
        or str(tags.get("conveying", "")).lower() in {"yes", "forward", "backward"}
        or str(tags.get("ramp", "")).lower() == "yes"
    )
    bridge = str(tags.get("bridge", "")).lower() == "yes"
    covered = str(tags.get("covered", "")).lower() == "yes"
    nonzero_layer = layer not in {"0", "+0", "-0"}
    explicit_layer = bridge or covered or transition or nonzero_layer
    if transition:
        level_key = "transition-{}-layer-{}".format(highway or "path", layer)
    elif bridge or nonzero_layer:
        level_key = "deck-layer-{}".format(layer)
    else:
        level_key = "ground-layer-0"
    ground_sidewalk = (
        not explicit_layer
        and (
            classification_name(candidate["classification"]) == "sidewalk"
            or str(tags.get("footway", "")).lower() == "sidewalk"
        )
    )
    return {
        "level_key": stable_id(level_key),
        "layer": layer,
        "highway": highway,
        "transition": transition,
        "bridge": bridge,
        "covered": covered,
        "nonzero_layer": nonzero_layer,
        "explicit_layer": explicit_layer,
        "ground_sidewalk": ground_sidewalk,
        "seed_eligible": explicit_layer,
    }


def semantic_recovery_attempt_state(
    plan: dict[str, Any], results: dict[str, Any]
) -> dict[str, Any]:
    """Summarize resolved full-chain attempts by source, start, and offset.

    A failed direction becomes unavailable to the normal alternative-cut
    planner only after every base offset for that direction has been resolved
    and no complete chain exists. Ground sidewalks may later re-enter the
    graph one finite registration tier at a time; structured/non-sidewalk
    paths never receive a lateral retry.
    """

    candidate_by_id = {
        candidate["candidate_id"]: candidate for candidate in plan["candidates"]
    }
    original = [
        candidate
        for candidate in plan["candidates"]
        if candidate.get("topology_origin") == "openstreetmap"
    ]
    chains: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for candidate in plan["candidates"]:
        if candidate.get("topology_origin") == "osm-semantic-recovery":
            chains[candidate["semantic_chain_id"]].append(candidate)

    attempted_variants: dict[tuple[str, float, str], str] = {}
    evidence_qualified_variants: set[tuple[str, float, str]] = set()
    complete_source_ids: set[str] = set()
    for chain in chains.values():
        chain.sort(key=lambda candidate: int(candidate["semantic_segment_index"]))
        expected_count = int(chain[0]["semantic_segment_count"])
        indices = {int(candidate["semantic_segment_index"]) for candidate in chain}
        if len(chain) != expected_count or indices != set(range(expected_count)):
            continue
        chain_results = [results.get(candidate["candidate_id"]) for candidate in chain]
        if any(
            result is None or result.get("status") not in {"accepted", "rejected"}
            for result in chain_results
        ):
            continue
        first = chain[0]
        key = (
            first["semantic_source_candidate_id"],
            round(float(first["tags"]["semantic_lateral_offset_cm"]), 4),
            first["semantic_start_point_id"],
        )
        status = (
            "accepted"
            if all(result["status"] == "accepted" for result in chain_results)
            else "rejected"
        )
        previous = attempted_variants.get(key)
        if previous == "accepted" or (previous is not None and previous != status):
            raise CertificationError(
                "semantic recovery variant has conflicting terminal results: {}".format(
                    key
                )
            )
        attempted_variants[key] = status
        if status == "accepted":
            complete_source_ids.add(first["semantic_source_candidate_id"])
        else:
            rejected_results = [
                result
                for result in chain_results
                if result.get("status") == "rejected"
            ]
            has_real_failure_station = any(
                isinstance(result.get("failure_evidence"), dict)
                and isinstance(
                    result["failure_evidence"].get("station_distance_cm"),
                    (int, float),
                )
                and float(result["failure_evidence"]["station_distance_cm"]) > 0.0
                for result in rejected_results
            )
            crosses_missing_support = any(
                result.get("reason") == "missing_support"
                for result in rejected_results
            )
            if has_real_failure_station and not crosses_missing_support:
                evidence_qualified_variants.add(key)

    base_blocked_directions: set[tuple[str, str]] = set()
    fully_blocked_directions: set[tuple[str, str]] = set()
    expansion_eligible_directions: set[tuple[str, str]] = set()
    direction_summaries = []
    for source in original:
        source_id = source["candidate_id"]
        if results.get(source_id, {}).get("status") != "rejected":
            continue
        profile = semantic_profile(source)
        base_offsets = (
            SEMANTIC_SIDEWALK_OFFSETS_CM
            if profile["ground_sidewalk"]
            else (0.0,)
        )
        expansion_offsets = tuple(
            offset
            for magnitude in SEMANTIC_SIDEWALK_EXPANSION_MAGNITUDES_CM
            for offset in (magnitude, -magnitude)
        ) if profile["ground_sidewalk"] else ()
        for start_point_id in (
            source["from_point_id"],
            source["to_point_id"],
        ):
            statuses = {
                offset: attempted_variants.get(
                    (source_id, round(float(offset), 4), start_point_id)
                )
                for offset in base_offsets + expansion_offsets
            }
            base_exhausted = (
                source_id not in complete_source_ids
                and all(statuses[offset] == "rejected" for offset in base_offsets)
            )
            evidence_qualified = all(
                (
                    source_id,
                    round(float(offset), 4),
                    start_point_id,
                )
                in evidence_qualified_variants
                for offset in base_offsets
            )
            expansion_eligible = (
                profile["ground_sidewalk"]
                and base_exhausted
                and evidence_qualified
            )
            fully_exhausted = (
                base_exhausted
                and (
                    not expansion_eligible
                    or all(
                        statuses[offset] == "rejected"
                        for offset in expansion_offsets
                    )
                )
            )
            if base_exhausted:
                base_blocked_directions.add((source_id, start_point_id))
            if fully_exhausted:
                fully_blocked_directions.add((source_id, start_point_id))
            if expansion_eligible:
                expansion_eligible_directions.add((source_id, start_point_id))
            if any(status is not None for status in statuses.values()):
                direction_summaries.append(
                    {
                        "source_candidate_id": source_id,
                        "start_point_id": start_point_id,
                        "ground_sidewalk": profile["ground_sidewalk"],
                        "base_exhausted": base_exhausted,
                        "fully_exhausted": fully_exhausted,
                        "expansion_eligible": expansion_eligible,
                        "attempted_offsets": {
                            str(offset): status
                            for offset, status in statuses.items()
                            if status is not None
                        },
                    }
                )

    return {
        "attempted_variants": attempted_variants,
        "evidence_qualified_variants": evidence_qualified_variants,
        "complete_source_ids": complete_source_ids,
        "base_blocked_directions": base_blocked_directions,
        "fully_blocked_directions": fully_blocked_directions,
        "expansion_eligible_directions": expansion_eligible_directions,
        "direction_summaries": direction_summaries,
        "candidate_by_id": candidate_by_id,
    }


def semantic_offsets_for_next_attempt(
    source: dict[str, Any],
    start_point_id: str,
    attempt_state: dict[str, Any],
    *,
    allow_expansion: bool,
) -> tuple[float, ...]:
    """Return only the smallest untried offset tier for one orientation."""

    source_id = source["candidate_id"]
    profile = semantic_profile(source)
    tiers: list[tuple[float, ...]] = [
        SEMANTIC_SIDEWALK_OFFSETS_CM
        if profile["ground_sidewalk"]
        else (0.0,)
    ]
    if allow_expansion and profile["ground_sidewalk"]:
        tiers.extend(
            (magnitude, -magnitude)
            for magnitude in SEMANTIC_SIDEWALK_EXPANSION_MAGNITUDES_CM
        )
    attempted = attempt_state["attempted_variants"]
    for tier_index, tier in enumerate(tiers):
        if (
            tier_index > 0
            and (source_id, start_point_id)
            not in attempt_state["expansion_eligible_directions"]
        ):
            return ()
        untried = tuple(
            float(offset)
            for offset in tier
            if (
                source_id,
                round(float(offset), 4),
                start_point_id,
            )
            not in attempted
        )
        if untried:
            return untried
    return ()


def semantic_level_conflicts(
    original_candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Detect accidental same-node merges across non-transition levels."""

    incident: dict[str, list[tuple[dict[str, Any], dict[str, Any]]]] = defaultdict(list)
    for candidate in original_candidates:
        profile = semantic_profile(candidate)
        incident[candidate["from_point_id"]].append((candidate, profile))
        incident[candidate["to_point_id"]].append((candidate, profile))
    conflicts = []
    for node_id, records in sorted(incident.items()):
        non_transition_levels = sorted(
            {profile["level_key"] for _candidate, profile in records if not profile["transition"]}
        )
        has_transition = any(profile["transition"] for _candidate, profile in records)
        if len(non_transition_levels) > 1 and not has_transition:
            conflicts.append(
                {
                    "node_id": node_id,
                    "non_transition_levels": non_transition_levels,
                    "candidate_ids": sorted(
                        candidate["candidate_id"] for candidate, _profile in records
                    ),
                }
            )
    return conflicts


def minimum_semantic_recovery_cut(
    plan: dict[str, Any],
    results: dict[str, Any],
    *,
    blocked_directions: set[tuple[str, str]] | frozenset[tuple[str, str]] | None = None,
) -> dict[str, Any]:
    """Find the lexicographically minimum rejected-original six-cell tree.

    Accepted original and complete semantic-recovery chains are contracted at
    zero cost. Generated connectors, incomplete chains, and duplicate offset
    variants never contribute to the runtime topology.
    Only rejected original OSM edges may be selected.  Terminals must belong
    to an already collision-certified component, so an isolated raw OSM node
    can never satisfy a cell merely because its source XY lies in that cell.
    """

    blocked_directions = set(blocked_directions or ())
    candidates = {
        candidate["candidate_id"]: candidate for candidate in plan["candidates"]
    }
    original = [
        candidate
        for candidate in plan["candidates"]
        if candidate.get("topology_origin") == "openstreetmap"
    ]
    nodes = sorted(
        {
            node_id
            for candidate in plan["candidates"]
            for node_id in (candidate["from_point_id"], candidate["to_point_id"])
        }
    )
    parent = {node_id: node_id for node_id in nodes}

    def find(node_id: str) -> str:
        while parent[node_id] != node_id:
            parent[node_id] = parent[parent[node_id]]
            node_id = parent[node_id]
        return node_id

    def union(first: str, second: str) -> None:
        first_root = find(first)
        second_root = find(second)
        if first_root != second_root:
            if first_root > second_root:
                first_root, second_root = second_root, first_root
            parent[second_root] = first_root

    trusted_ids = trusted_runtime_result_ids(plan, results)
    accepted_ids = []
    for candidate_id, result in results.items():
        candidate = candidates.get(candidate_id)
        if (
            candidate is None
            or candidate_id not in trusted_ids
            or result.get("status") != "accepted"
            or candidate.get("topology_origin")
            not in TRUSTED_RUNTIME_TOPOLOGY_ORIGINS
        ):
            continue
        union(candidate["from_point_id"], candidate["to_point_id"])
        accepted_ids.append(candidate_id)

    accepted_roots = set()
    for candidate_id in accepted_ids:
        candidate = candidates[candidate_id]
        accepted_roots.add(find(candidate["from_point_id"]))

    cell_index = {cell_id: index for index, cell_id in enumerate(plan["cell_ids"])}
    terminal_masks: dict[str, int] = defaultdict(int)
    for node_id in nodes:
        root = find(node_id)
        if root not in accepted_roots:
            continue
        cell_id = plan["node_owner"].get(node_id)
        if cell_id in cell_index:
            terminal_masks[root] |= 1 << cell_index[cell_id]

    roots = sorted({find(node_id) for node_id in nodes})
    root_index = {root: index for index, root in enumerate(roots)}
    adjacency: list[list[tuple[int, str, float, str]]] = [[] for _root in roots]
    rejected_original = []
    internal_rejected = 0
    for candidate in original:
        result = results.get(candidate["candidate_id"])
        if result is None or result.get("status") != "rejected":
            continue
        first_root = find(candidate["from_point_id"])
        second_root = find(candidate["to_point_id"])
        if first_root == second_root:
            internal_rejected += 1
            continue
        first_index = root_index[first_root]
        second_index = root_index[second_root]
        length = float(candidate["source_length_xy_cm"])
        candidate_id = candidate["candidate_id"]
        if (candidate_id, candidate["from_point_id"]) not in blocked_directions:
            adjacency[first_index].append(
                (second_index, candidate_id, length, candidate["from_point_id"])
            )
        if (candidate_id, candidate["to_point_id"]) not in blocked_directions:
            adjacency[second_index].append(
                (first_index, candidate_id, length, candidate["to_point_id"])
            )
        rejected_original.append(candidate)

    full_mask = (1 << len(plan["cell_ids"])) - 1
    infinity = (10**9, math.inf)
    dp: list[dict[int, tuple[int, float]]] = [dict() for _mask in range(full_mask + 1)]
    predecessor: list[dict[int, tuple[Any, ...]]] = [
        dict() for _mask in range(full_mask + 1)
    ]
    for root, terminal_mask in terminal_masks.items():
        if terminal_mask:
            vertex = root_index[root]
            dp[terminal_mask][vertex] = (0, 0.0)
            predecessor[terminal_mask][vertex] = ("terminal",)

    def add_cost(
        first: tuple[int, float], second: tuple[int, float]
    ) -> tuple[int, float]:
        return (first[0] + second[0], first[1] + second[1])

    for mask in range(1, full_mask + 1):
        submask = (mask - 1) & mask
        while submask:
            other = mask ^ submask
            if other and submask <= other:
                common = set(dp[submask]).intersection(dp[other])
                for vertex in common:
                    cost = add_cost(dp[submask][vertex], dp[other][vertex])
                    if cost < dp[mask].get(vertex, infinity):
                        dp[mask][vertex] = cost
                        predecessor[mask][vertex] = (
                            "merge",
                            submask,
                            other,
                        )
            submask = (submask - 1) & mask

        queue = [
            (cost[0], cost[1], vertex) for vertex, cost in dp[mask].items()
        ]
        heapq.heapify(queue)
        while queue:
            edge_count, length, vertex = heapq.heappop(queue)
            if (edge_count, length) != dp[mask].get(vertex):
                continue
            for neighbor, candidate_id, edge_length, start_point_id in adjacency[vertex]:
                cost = (edge_count + 1, length + edge_length)
                if cost < dp[mask].get(neighbor, infinity):
                    dp[mask][neighbor] = cost
                    predecessor[mask][neighbor] = (
                        "move",
                        vertex,
                        candidate_id,
                        start_point_id,
                    )
                    heapq.heappush(queue, (cost[0], cost[1], neighbor))

    if not dp[full_mask]:
        return {
            "feasible": False,
            "cut_candidate_ids": [],
            "accepted_component_count": len(accepted_roots),
            "rejected_original_edge_count": len(rejected_original),
            "internal_rejected_edge_count": internal_rejected,
            "blocked_direction_count": len(blocked_directions),
            "level_conflicts": semantic_level_conflicts(original),
        }
    best_vertex = min(dp[full_mask], key=lambda vertex: dp[full_mask][vertex])
    best_cost = dp[full_mask][best_vertex]
    selected: set[str] = set()
    selected_directions: set[tuple[str, str]] = set()
    visited_states: set[tuple[int, int]] = set()

    def collect(mask: int, vertex: int) -> None:
        state = (mask, vertex)
        if state in visited_states:
            return
        visited_states.add(state)
        entry = predecessor[mask][vertex]
        if entry[0] == "terminal":
            return
        if entry[0] == "merge":
            collect(int(entry[1]), vertex)
            collect(int(entry[2]), vertex)
            return
        previous_vertex = int(entry[1])
        selected.add(str(entry[2]))
        selected_directions.add((str(entry[2]), str(entry[3])))
        collect(mask, previous_vertex)

    collect(full_mask, best_vertex)
    selected_candidates = [candidates[candidate_id] for candidate_id in sorted(selected)]
    reason_counts: dict[str, int] = defaultdict(int)
    for candidate in selected_candidates:
        reason_counts[results[candidate["candidate_id"]]["reason"]] += 1
    return {
        "feasible": True,
        "cut_candidate_ids": [
            candidate["candidate_id"] for candidate in selected_candidates
        ],
        "rejected_edge_count": int(best_cost[0]),
        "total_length_cm": round(float(best_cost[1]), 3),
        "unique_rejected_edge_count": len(selected_candidates),
        "reason_counts": dict(sorted(reason_counts.items())),
        "accepted_component_count": len(accepted_roots),
        "rejected_original_edge_count": len(rejected_original),
        "internal_rejected_edge_count": internal_rejected,
        "blocked_direction_count": len(blocked_directions),
        "cut_directions": [
            {
                "source_candidate_id": candidate_id,
                "start_point_id": start_point_id,
            }
            for candidate_id, start_point_id in sorted(selected_directions)
        ],
        "level_conflicts": semantic_level_conflicts(original),
    }


def semantic_recovery_plan(
    plan: dict[str, Any],
    results: dict[str, Any],
    *,
    blocked_directions: set[tuple[str, str]] | frozenset[tuple[str, str]] | None = None,
) -> dict[str, Any]:
    """Extend the six-cell cut until trusted directional length can reach 3 km."""

    blocked_directions = set(blocked_directions or ())
    connectivity = minimum_semantic_recovery_cut(
        plan, results, blocked_directions=blocked_directions
    )
    if not connectivity.get("feasible"):
        return connectivity
    candidate_by_id = {
        candidate["candidate_id"]: candidate for candidate in plan["candidates"]
    }
    original = [
        candidate
        for candidate in plan["candidates"]
        if candidate.get("topology_origin") == "openstreetmap"
    ]
    nodes = sorted(
        {
            node_id
            for candidate in plan["candidates"]
            for node_id in (candidate["from_point_id"], candidate["to_point_id"])
        }
    )
    parent = {node_id: node_id for node_id in nodes}

    def find(node_id: str) -> str:
        while parent[node_id] != node_id:
            parent[node_id] = parent[parent[node_id]]
            node_id = parent[node_id]
        return node_id

    def union(first: str, second: str) -> None:
        first_root = find(first)
        second_root = find(second)
        if first_root != second_root:
            if first_root > second_root:
                first_root, second_root = second_root, first_root
            parent[second_root] = first_root

    trusted_ids = trusted_runtime_result_ids(plan, results)
    accepted_pairs = []
    for candidate_id, result in results.items():
        candidate = candidate_by_id.get(candidate_id)
        if (
            candidate is None
            or candidate_id not in trusted_ids
            or result.get("status") != "accepted"
            or candidate.get("topology_origin")
            not in TRUSTED_RUNTIME_TOPOLOGY_ORIGINS
        ):
            continue
        accepted_pairs.append((candidate, result))
        union(candidate["from_point_id"], candidate["to_point_id"])

    connectivity_ids = set(connectivity["cut_candidate_ids"])
    selected_ids = set(connectivity_ids)
    for candidate_id in sorted(selected_ids):
        candidate = candidate_by_id[candidate_id]
        union(candidate["from_point_id"], candidate["to_point_id"])

    def component_metrics() -> tuple[str | None, float]:
        lengths: dict[str, float] = defaultdict(float)
        cells: dict[str, set[str]] = defaultdict(set)
        for candidate, result in accepted_pairs:
            lengths[find(candidate["from_point_id"])] += polyline_length(
                result["samples"]
            )
        for candidate_id in selected_ids:
            candidate = candidate_by_id[candidate_id]
            lengths[find(candidate["from_point_id"])] += float(
                candidate["source_length_xy_cm"]
            )
        for node_id, cell_id in plan["node_owner"].items():
            if node_id in parent:
                cells[find(node_id)].add(cell_id)
        eligible = [
            root for root, cell_ids in cells.items() if len(cell_ids) == CELL_COUNT
        ]
        if not eligible:
            return None, 0.0
        root = max(eligible, key=lambda value: (lengths[value], value))
        return root, lengths[root]

    expansion_iterations = []
    for _iteration in range(len(original)):
        main_root, projected_length = component_metrics()
        if main_root is None:
            raise CertificationError("semantic connectivity cut did not span six cells")
        if projected_length >= MINIMUM_TRUSTED_UNDIRECTED_LENGTH_CM:
            break
        roots = sorted({find(node_id) for node_id in nodes})
        root_index = {root: index for index, root in enumerate(roots)}
        adjacency: list[list[tuple[int, dict[str, Any]]]] = [
            [] for _root in roots
        ]
        accepted_prize: dict[str, float] = defaultdict(float)
        for candidate, result in accepted_pairs:
            accepted_prize[find(candidate["from_point_id"])] += polyline_length(
                result["samples"]
            )
        for candidate in original:
            if candidate["candidate_id"] in selected_ids:
                continue
            result = results.get(candidate["candidate_id"])
            if result is None or result.get("status") != "rejected":
                continue
            first_root = find(candidate["from_point_id"])
            second_root = find(candidate["to_point_id"])
            if first_root == second_root:
                continue
            first_index = root_index[first_root]
            second_index = root_index[second_root]
            candidate_id = candidate["candidate_id"]
            if (candidate_id, candidate["from_point_id"]) not in blocked_directions:
                adjacency[first_index].append((second_index, candidate))
            if (candidate_id, candidate["to_point_id"]) not in blocked_directions:
                adjacency[second_index].append((first_index, candidate))

        start = root_index[main_root]
        distances: list[tuple[int, float] | None] = [None] * len(roots)
        previous: list[tuple[int, str] | None] = [None] * len(roots)
        distances[start] = (0, 0.0)
        queue = [(0, 0.0, start)]
        while queue:
            edge_count, length, vertex = heapq.heappop(queue)
            if distances[vertex] != (edge_count, length):
                continue
            for neighbor, candidate in adjacency[vertex]:
                cost = (
                    edge_count + 1,
                    length + float(candidate["source_length_xy_cm"]),
                )
                if distances[neighbor] is None or cost < distances[neighbor]:
                    distances[neighbor] = cost
                    previous[neighbor] = (vertex, candidate["candidate_id"])
                    heapq.heappush(queue, (cost[0], cost[1], neighbor))

        targets = []
        for vertex, root in enumerate(roots):
            if vertex == start or distances[vertex] is None:
                continue
            prize = accepted_prize[root]
            if prize <= 0.0:
                continue
            edge_count, path_length = distances[vertex]
            projected_gain = prize + path_length
            targets.append(
                (
                    edge_count,
                    -projected_gain,
                    path_length,
                    root,
                    vertex,
                    prize,
                )
            )
        if not targets:
            break
        _count, _negative_gain, _path_length, target_root, target, prize = min(
            targets
        )
        path_ids = []
        while target != start:
            entry = previous[target]
            if entry is None:
                raise CertificationError("semantic length path reconstruction failed")
            target, candidate_id = entry
            path_ids.append(candidate_id)
        for candidate_id in path_ids:
            selected_ids.add(candidate_id)
            candidate = candidate_by_id[candidate_id]
            union(candidate["from_point_id"], candidate["to_point_id"])
        expansion_iterations.append(
            {
                "target_component_root": target_root,
                "path_candidate_ids": list(reversed(path_ids)),
                "path_length_cm": round(
                    sum(
                        float(candidate_by_id[candidate_id]["source_length_xy_cm"])
                        for candidate_id in path_ids
                    ),
                    3,
                ),
                "accepted_length_prize_cm": round(float(prize), 3),
            }
        )

    _main_root, projected_length = component_metrics()
    expansion_ids = sorted(selected_ids - connectivity_ids)
    output = copy.deepcopy(connectivity)
    output.update(
        {
            "connectivity_cut_candidate_ids": sorted(connectivity_ids),
            "length_expansion_candidate_ids": expansion_ids,
            "cut_candidate_ids": sorted(selected_ids),
            "length_expansion_edge_count": len(expansion_ids),
            "planned_rejected_edge_count": len(selected_ids),
            "projected_trusted_undirected_length_cm": round(projected_length, 3),
            "projected_trusted_directional_length_cm": round(
                projected_length * 2.0, 3
            ),
            "minimum_trusted_directional_length_cm": round(
                MINIMUM_TRUSTED_UNDIRECTED_LENGTH_CM * 2.0, 3
            ),
            "length_expansion_iterations": expansion_iterations,
        }
    )
    return output


def _semantic_endpoint_levels(
    candidate: dict[str, Any], original_candidates: list[dict[str, Any]]
) -> tuple[str, str]:
    profile = semantic_profile(candidate)
    if not profile["transition"]:
        return profile["level_key"], profile["level_key"]
    incident: dict[str, set[str]] = defaultdict(set)
    endpoints = {candidate["from_point_id"], candidate["to_point_id"]}
    for other in original_candidates:
        if other["candidate_id"] == candidate["candidate_id"]:
            continue
        other_profile = semantic_profile(other)
        if other_profile["transition"]:
            continue
        for node_id in (other["from_point_id"], other["to_point_id"]):
            if node_id in endpoints:
                incident[node_id].add(other_profile["level_key"])

    def endpoint_level(node_id: str) -> str:
        values = sorted(incident[node_id])
        return values[0] if len(values) == 1 else profile["level_key"]

    return (
        endpoint_level(candidate["from_point_id"]),
        endpoint_level(candidate["to_point_id"]),
    )


def _semantic_offset_token(offset_cm: float) -> str:
    if abs(offset_cm) <= 1.0e-6:
        return "z000"
    return ("p" if offset_cm > 0.0 else "m") + "{:03d}".format(
        int(round(abs(offset_cm)))
    )


def split_semantic_recovery_candidate(
    source: dict[str, Any],
    plan: dict[str, Any],
    settings: dict[str, Any],
    *,
    lateral_offset_cm: float,
    round_number: int,
    start_is_certified: bool,
) -> list[dict[str, Any]]:
    """Split one OSM edge into a tapered, level-aware exact-trace chain."""

    profile = semantic_profile(source)
    if abs(lateral_offset_cm) > 1.0e-6 and not profile["ground_sidewalk"]:
        raise CertificationError("lateral semantic offset is ground-sidewalk-only")
    maximum_length = (
        SEMANTIC_LAYER_SEGMENT_MAX_CM
        if profile["explicit_layer"]
        else SEMANTIC_GROUND_SEGMENT_MAX_CM
    )
    source_length = float(source["source_length_xy_cm"])
    segment_count = max(1, int(math.ceil(source_length / maximum_length)))
    if abs(lateral_offset_cm) > 1.0e-6:
        segment_count = max(2, segment_count)
    first = source["from_source_position"]
    second = source["to_source_position"]
    direction_x = (float(second[0]) - float(first[0])) / source_length
    direction_y = (float(second[1]) - float(first[1])) / source_length
    normal = [-direction_y, direction_x]
    offset_token = _semantic_offset_token(lateral_offset_cm)
    original_candidates = [
        candidate
        for candidate in plan["candidates"]
        if candidate.get("topology_origin") == "openstreetmap"
    ]
    from_level, to_level = _semantic_endpoint_levels(source, original_candidates)
    chain_id = stable_id(
        "semantic-chain-{}-{}-start-{}".format(
            source["candidate_id"], offset_token, source["from_point_id"]
        )
    )
    positions = []
    point_ids = []
    for index in range(segment_count + 1):
        alpha = index / segment_count
        taper = math.sin(math.pi * alpha)
        positions.append(
            [
                lerp(float(first[0]), float(second[0]), alpha)
                + normal[0] * lateral_offset_cm * taper,
                lerp(float(first[1]), float(second[1]), alpha)
                + normal[1] * lateral_offset_cm * taper,
                lerp(float(first[2]), float(second[2]), alpha),
            ]
        )
        if index == 0:
            point_ids.append(source["from_point_id"])
        elif index == segment_count:
            point_ids.append(source["to_point_id"])
        else:
            point_ids.append(
                stable_id(
                    "{}-{}-station-{:03d}".format(
                        chain_id, profile["level_key"], index
                    )
                )
            )

    output = []
    for index in range(segment_count):
        from_id = point_ids[index]
        to_id = point_ids[index + 1]
        from_cell = plan["node_owner"].get(from_id, source["source_cell_id"])
        to_cell = plan["node_owner"].get(to_id, source["source_cell_id"])
        tags = copy.deepcopy(source.get("tags", {}))
        tags.update(
            {
                "semantic_recovery": "original-osm-polyline",
                "semantic_source_candidate_id": source["candidate_id"],
                "semantic_chain_id": chain_id,
                "semantic_start_point_id": source["from_point_id"],
                "semantic_level_key": profile["level_key"],
                "semantic_from_level": from_level,
                "semantic_to_level": to_level,
                "semantic_lateral_offset_cm": round(float(lateral_offset_cm), 4),
                "semantic_round": round_number,
                "semantic_segment_index": index,
                "semantic_segment_count": segment_count,
            }
        )
        seed_policy = "certified-predecessor-required"
        if index == 0 and not start_is_certified and profile["seed_eligible"]:
            seed_policy = "explicit-layer-once"
        candidate = {
            "candidate_id": stable_id(
                "candidate-{}-segment-{:03d}".format(chain_id, index)
            ),
            "source_feature_id": source["source_feature_id"],
            "source_cell_id": source["source_cell_id"],
            "classification": source["classification"],
            "topology_origin": "osm-semantic-recovery",
            "from_point_id": from_id,
            "to_point_id": to_id,
            "from_source_position": round_vector(positions[index]),
            "to_source_position": round_vector(positions[index + 1]),
            "source_length_xy_cm": distance_xy(positions[index], positions[index + 1]),
            "from_cell_id": from_cell,
            "to_cell_id": to_cell,
            "tags": tags,
            "requires_manual_review": False,
            "semantic_seed_policy": seed_policy,
            "semantic_source_candidate_id": source["candidate_id"],
            "semantic_chain_id": chain_id,
            "semantic_start_point_id": source["from_point_id"],
            "semantic_segment_index": index,
            "semantic_segment_count": segment_count,
        }
        output.append(candidate)
        plan["node_owner"].setdefault(from_id, from_cell)
        plan["node_owner"].setdefault(to_id, to_cell)
    return output


def _register_semantic_nodes(
    plan: dict[str, Any], candidates: list[dict[str, Any]]
) -> None:
    for candidate in candidates:
        plan["node_owner"].setdefault(
            candidate["from_point_id"],
            candidate.get("from_cell_id", candidate["source_cell_id"]),
        )
        plan["node_owner"].setdefault(
            candidate["to_point_id"],
            candidate.get("to_cell_id", candidate["source_cell_id"]),
        )


def _accepted_semantic_levels_by_node(
    plan: dict[str, Any], results: dict[str, Any]
) -> dict[str, set[str]]:
    levels: dict[str, set[str]] = defaultdict(set)
    candidate_by_id = {
        candidate["candidate_id"]: candidate for candidate in plan["candidates"]
    }
    for candidate_id, result in results.items():
        candidate = candidate_by_id.get(candidate_id)
        if (
            candidate is None
            or result.get("status") != "accepted"
            or candidate.get("topology_origin")
            not in TRUSTED_RUNTIME_TOPOLOGY_ORIGINS
        ):
            continue
        profile = semantic_profile(candidate)
        if profile["transition"]:
            continue
        levels[candidate["from_point_id"]].add(profile["level_key"])
        levels[candidate["to_point_id"]].add(profile["level_key"])
    return levels


def augment_plan_with_semantic_recovery(
    plan: dict[str, Any],
    work: dict[str, Any],
    settings: dict[str, Any],
    *,
    work_path: Path = WORK_PATH,
) -> int:
    """Queue only OSM-topology recovery chains selected by the six-cell cut."""

    stored = copy.deepcopy(work.get("semantic_recovery_candidates", []))
    if stored:
        _register_semantic_nodes(plan, stored)
        plan["candidates"].extend(copy.deepcopy(stored))
    expected_ids = {candidate["candidate_id"] for candidate in plan["candidates"]}
    if set(work.get("results", {})) != expected_ids:
        return len(stored)
    trusted_ids = trusted_runtime_result_ids(plan, work["results"])
    components = accepted_components(
        plan,
        work["results"],
        allowed_origins=RUNTIME_ELIGIBLE_TOPOLOGY_ORIGINS,
        allowed_candidate_ids=trusted_ids,
    )
    if any(
        len(component["cells"]) == CELL_COUNT
        and component["length_cm"] >= MINIMUM_TRUSTED_UNDIRECTED_LENGTH_CM
        for component in components
    ):
        return len(stored)
    completed_rounds = int(work.get("semantic_recovery_round_count", 0))
    if completed_rounds >= SEMANTIC_RECOVERY_MAX_ROUNDS:
        return len(stored)
    attempt_state = semantic_recovery_attempt_state(plan, work["results"])
    candidate_by_id = {
        candidate["candidate_id"]: candidate for candidate in plan["candidates"]
    }
    trusted_results = {
        candidate_id: work["results"][candidate_id]
        for candidate_id in trusted_ids
    }
    known_nodes = rebuild_known_nodes(
        trusted_results, allowed_origins=TRUSTED_RUNTIME_TOPOLOGY_ORIGINS
    )
    accepted_levels = _accepted_semantic_levels_by_node(plan, trusted_results)
    round_number = completed_rounds + 1
    existing_seeded_level_keys = {
        semantic_profile(candidate)["level_key"]
        for candidate in stored
        if candidate.get("semantic_seed_policy") == "explicit-layer-once"
    }

    def build_proposals(
        recovery_cut: dict[str, Any], *, allow_expansion: bool
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        if not recovery_cut.get("feasible"):
            return [], []
        proposals: list[dict[str, Any]] = []
        planned_sources: list[dict[str, Any]] = []
        seeded_level_keys = set(existing_seeded_level_keys)
        for source_id in recovery_cut["cut_candidate_ids"]:
            source = candidate_by_id[source_id]
            profile = semantic_profile(source)
            orientations = []
            if source["from_point_id"] in known_nodes:
                orientations.append(copy.deepcopy(source))
            if source["to_point_id"] in known_nodes:
                orientations.append(reverse_candidate(source))
            if (
                not orientations
                and profile["seed_eligible"]
                and profile["level_key"] not in seeded_level_keys
            ):
                # One explicit Cesium seed is permitted per semantic level;
                # ground routes can never seed from ellipsoid/source Z.
                orientations.append(copy.deepcopy(source))
            orientations = [
                oriented
                for oriented in orientations
                if profile["transition"]
                or not accepted_levels.get(oriented["from_point_id"])
                or profile["level_key"]
                in accepted_levels[oriented["from_point_id"]]
            ]
            for oriented in orientations:
                offsets = semantic_offsets_for_next_attempt(
                    source,
                    oriented["from_point_id"],
                    attempt_state,
                    allow_expansion=allow_expansion,
                )
                if not offsets:
                    continue
                start_is_certified = oriented["from_point_id"] in known_nodes
                if (
                    not start_is_certified
                    and profile["seed_eligible"]
                    and profile["level_key"] in seeded_level_keys
                ):
                    continue
                for offset in offsets:
                    chain = split_semantic_recovery_candidate(
                        oriented,
                        plan,
                        settings,
                        lateral_offset_cm=float(offset),
                        round_number=round_number,
                        start_is_certified=start_is_certified,
                    )
                    proposals.extend(chain)
                    planned_sources.append(
                        {
                            "source_candidate_id": source_id,
                            "start_point_id": oriented["from_point_id"],
                            "end_point_id": oriented["to_point_id"],
                            "lateral_offset_cm": float(offset),
                            "offset_tier": (
                                "base"
                                if abs(float(offset)) <= 20.0
                                else "registration-{}cm".format(
                                    int(round(abs(float(offset))))
                                )
                            ),
                            "segment_count": len(chain),
                            "semantic_level_key": profile["level_key"],
                            "seed_policy": chain[0]["semantic_seed_policy"],
                        }
                    )
                    if chain[0]["semantic_seed_policy"] == "explicit-layer-once":
                        seeded_level_keys.add(profile["level_key"])
        return proposals, planned_sources

    base_blocked = attempt_state["base_blocked_directions"]
    cut = semantic_recovery_plan(
        plan, work["results"], blocked_directions=base_blocked
    )
    recovery_mode = "base" if not base_blocked else "alternative-cut"
    proposals, planned_sources = build_proposals(cut, allow_expansion=False)
    base_cut_feasible = bool(cut.get("feasible"))
    expanded_cut_feasible = False
    if not proposals:
        expanded_cut = semantic_recovery_plan(
            plan,
            work["results"],
            blocked_directions=attempt_state["fully_blocked_directions"],
        )
        expanded_cut_feasible = bool(expanded_cut.get("feasible"))
        expanded_proposals, expanded_sources = build_proposals(
            expanded_cut, allow_expansion=True
        )
        if expanded_proposals:
            cut = expanded_cut
            proposals = expanded_proposals
            planned_sources = expanded_sources
            recovery_mode = "ground-sidewalk-registration"
        else:
            # If the fully exhausted structured graph has no six-cell tree,
            # continue only the finite ground-sidewalk registration tiers on
            # the raw OSM cut. This can unlock a different trusted frontier,
            # but never retries a bridge/steps/layer failure or crosses a
            # missing-support interval.
            relaxed_cut = semantic_recovery_plan(plan, work["results"])
            registration_cut = copy.deepcopy(relaxed_cut)
            registration_cut["cut_candidate_ids"] = list(
                relaxed_cut.get(
                    "connectivity_cut_candidate_ids",
                    relaxed_cut.get("cut_candidate_ids", []),
                )
            )
            relaxed_proposals, relaxed_sources = build_proposals(
                registration_cut, allow_expansion=True
            )
            if relaxed_proposals:
                cut = relaxed_cut
                proposals = relaxed_proposals
                planned_sources = relaxed_sources
                recovery_mode = "ground-sidewalk-registration"
                cut["registration_scope"] = "connectivity-cut-only"
            elif not base_cut_feasible:
                cut = expanded_cut
                recovery_mode = "exhausted"

    cut["recovery_mode"] = recovery_mode
    cut["base_cut_feasible"] = base_cut_feasible
    cut["expanded_cut_feasible"] = expanded_cut_feasible
    cut["base_blocked_direction_count"] = len(base_blocked)
    cut["fully_blocked_direction_count"] = len(
        attempt_state["fully_blocked_directions"]
    )
    cut["attempted_semantic_variant_count"] = len(
        attempt_state["attempted_variants"]
    )
    work["semantic_recovery_planner"] = copy.deepcopy(cut)
    if not proposals:
        work["updated_at_utc"] = utc_timestamp()
        write_json_atomic(work_path, work)
        return len(stored)
    work.setdefault("semantic_recovery_rounds", []).append(
        {
            "round": round_number,
            "recovery_mode": recovery_mode,
            "cut_rejected_edge_count": cut.get("rejected_edge_count"),
            "cut_total_length_cm": cut.get("total_length_cm"),
            "source_variant_count": len(planned_sources),
            "segment_candidate_count": len(proposals),
            "planned_sources": planned_sources,
        }
    )
    work["semantic_recovery_round_count"] = round_number
    work["semantic_recovery_candidates"] = copy.deepcopy(stored + proposals)
    work["updated_at_utc"] = utc_timestamp()
    write_json_atomic(work_path, work)
    plan["candidates"].extend(copy.deepcopy(proposals))
    return len(stored) + len(proposals)


def augment_plan_with_generated_connectors(
    plan: dict[str, Any],
    work: dict[str, Any],
    settings: dict[str, Any],
    *,
    work_path: Path = WORK_PATH,
) -> int:
    """Queue short bridges between certified components for full recertification.

    Proximity never promotes a bridge. Every generated candidate enters the
    same Cesium support, continuity, slope, multi-track, and clearance pipeline
    as an OSM edge. The deterministic candidate list is persisted so editor
    restarts cannot silently change the network.
    """

    queue_key = lambda candidate: (
        candidate["source_cell_id"],
        candidate["source_length_xy_cm"],
        candidate["candidate_id"],
    )
    stored = copy.deepcopy(work.get("generated_connector_candidates", []))
    if stored:
        plan["candidates"].extend(sorted(copy.deepcopy(stored), key=queue_key))

    expected_ids = {candidate["candidate_id"] for candidate in plan["candidates"]}
    if set(work.get("results", {})) != expected_ids:
        return len(stored)

    components = accepted_components(plan, work["results"])
    if any(len(component["cells"]) == CELL_COUNT for component in components):
        return len(stored)

    completed_rounds = int(
        work.get("generated_connector_round_count", 1 if stored else 0)
    )
    if completed_rounds >= len(GENERATED_CONNECTOR_ROUNDS):
        return len(stored)
    policy = GENERATED_CONNECTOR_ROUNDS[completed_rounds]
    max_distance = float(policy["max_distance_cm"])
    max_vertical_delta = float(policy["max_vertical_delta_cm"])
    pairs_per_component_pair = int(policy["pairs_per_component_pair"])
    max_candidates = int(policy["max_candidates"])

    component_positions = [
        canonical_node_positions(component["pairs"])
        for component in components
    ]
    proposals: list[dict[str, Any]] = []
    seen_node_pairs: set[tuple[str, str]] = {
        tuple(sorted((candidate["from_point_id"], candidate["to_point_id"])))
        for candidate in stored
    }
    lane_offset = float(settings["lane_height_offset_cm"])

    for first_index, first_component in enumerate(components):
        first_positions = component_positions[first_index]
        for second_index in range(first_index + 1, len(components)):
            second_component = components[second_index]
            second_positions = component_positions[second_index]
            combined_cells = set(first_component["cells"]) | set(
                second_component["cells"]
            )
            largest_component_cell_count = max(
                len(first_component["cells"]), len(second_component["cells"])
            )
            cell_coverage_gain = (
                len(combined_cells) - largest_component_cell_count
            )
            nearby = []
            for first_node, first_position in first_positions.items():
                for second_node, second_position in second_positions.items():
                    distance = distance_xy(first_position, second_position)
                    vertical_delta = abs(
                        float(first_position[2]) - float(second_position[2])
                    )
                    if distance <= max_distance and vertical_delta <= max_vertical_delta:
                        nearby.append(
                            (
                                distance,
                                vertical_delta,
                                first_node,
                                second_node,
                                first_position,
                                second_position,
                            )
                        )
            nearby.sort(key=lambda item: (item[0], item[1], item[2], item[3]))
            admitted_for_pair = 0
            for (
                distance,
                vertical_delta,
                first_node,
                second_node,
                first_position,
                second_position,
            ) in nearby:
                node_pair = tuple(sorted((first_node, second_node)))
                if node_pair in seen_node_pairs:
                    continue
                seen_node_pairs.add(node_pair)
                connector_id = stable_id(
                    "generated-connector-{}-{}".format(node_pair[0], node_pair[1])
                )
                proposals.append(
                    {
                        "candidate_id": stable_id("candidate-" + connector_id),
                        "source_feature_id": connector_id,
                        "source_cell_id": plan["node_owner"][first_node],
                        "classification": "manual-pedestrian-link",
                        "topology_origin": "generated-connector",
                        "from_point_id": first_node,
                        "to_point_id": second_node,
                        "from_source_position": [
                            float(first_position[0]),
                            float(first_position[1]),
                            float(first_position[2]) - lane_offset,
                        ],
                        "to_source_position": [
                            float(second_position[0]),
                            float(second_position[1]),
                            float(second_position[2]) - lane_offset,
                        ],
                        "source_length_xy_cm": float(distance),
                        "tags": {
                            "generated": "collision-certified-component-bridge",
                            "round": completed_rounds + 1,
                            # These are queue-priority facts, never acceptance
                            # evidence. The bridge still has to pass the exact
                            # same dense Cesium trace/sweep pipeline as every
                            # source edge before it can join a component.
                            "cell_coverage_gain": cell_coverage_gain,
                            "cell_union_count": len(combined_cells),
                            "largest_component_cell_count": (
                                largest_component_cell_count
                            ),
                            "vertical_delta_cm": round(float(vertical_delta), 4),
                        },
                        "requires_manual_review": False,
                    }
                )
                admitted_for_pair += 1
                if admitted_for_pair >= pairs_per_component_pair:
                    break

    proposals.sort(
        key=lambda candidate: (
            # The candidate cap previously filled with the globally shortest
            # local fragment repairs. Reserve budget first for component
            # pairs whose *accepted* union could add Central cells, while
            # retaining distance as the physical plausibility tie-breaker.
            -int(candidate["tags"]["cell_coverage_gain"]),
            -int(candidate["tags"]["cell_union_count"]),
            -int(candidate["tags"]["largest_component_cell_count"]),
            candidate["source_length_xy_cm"],
            float(candidate["tags"]["vertical_delta_cm"]),
            candidate["candidate_id"],
        )
    )
    proposals = proposals[:max_candidates]
    work.setdefault("generated_connector_rounds", []).append(
        {
            "round": completed_rounds + 1,
            **copy.deepcopy(policy),
            "candidate_count": len(proposals),
        }
    )
    work["generated_connector_round_count"] = completed_rounds + 1
    work["generated_connector_candidates"] = copy.deepcopy(stored + proposals)
    work["updated_at_utc"] = utc_timestamp()
    write_json_atomic(work_path, work)
    plan["candidates"].extend(sorted(copy.deepcopy(proposals), key=queue_key))
    return len(stored) + len(proposals)


def finalize_lane_samples(
    lane_id: str,
    raw_samples: list[dict[str, Any]],
    start_position: list[float],
    end_position: list[float],
) -> tuple[list[dict[str, Any]], float]:
    samples = copy.deepcopy(raw_samples)
    samples[0]["center_position"] = copy.deepcopy(start_position)
    samples[-1]["center_position"] = copy.deepcopy(end_position)
    distances = [0.0]
    for first, second in zip(samples, samples[1:]):
        distances.append(
            distances[-1]
            + vector_distance(first["center_position"], second["center_position"])
        )
    for index, sample in enumerate(samples):
        neighbor_deltas = []
        tracks = (
            sample["center_position"],
            sample["left_track_position"],
            sample["right_track_position"],
        )
        neighbor_deltas.extend(
            abs(float(tracks[a][2]) - float(tracks[b][2]))
            for a, b in ((0, 1), (0, 2), (1, 2))
        )
        for neighbor_index in (index - 1, index + 1):
            if 0 <= neighbor_index < len(samples):
                neighbor = samples[neighbor_index]
                neighbor_tracks = (
                    neighbor["center_position"],
                    neighbor["left_track_position"],
                    neighbor["right_track_position"],
                )
                neighbor_deltas.extend(
                    abs(float(tracks[track][2]) - float(neighbor_tracks[track][2]))
                    for track in range(3)
                )
        normal = sample["surface_normal"]
        normal_z = max(-1.0, min(1.0, float(normal[2])))
        sample.update(
            {
                "sample_id": stable_id("sample-{}-{:05d}".format(lane_id, index)),
                "sample_index": index,
                "distance_along_lane_cm": round(distances[index], 4),
                "surface_slope_degrees": round(
                    math.degrees(math.acos(normal_z)), 4
                ),
                "max_neighbor_height_delta_cm": round(
                    max(neighbor_deltas, default=0.0), 4
                ),
                "evidence_mask": FULL_GROUND_EVIDENCE_MASK,
            }
        )
    return samples, round(distances[-1], 4)


def empty_evidence() -> dict[str, Any]:
    return {
        "candidate_lane_count": 0,
        "certified_lane_count": 0,
        "rejected_lane_count": 0,
        "coarse_support_check_count": 0,
        "strict_ground_sample_count": 0,
        "exact_xy_support_pass_count": 0,
        "first_blocker_pass_count": 0,
        "height_continuity_pass_count": 0,
        "slope_pass_count": 0,
        "multi_track_support_pass_count": 0,
        "capsule_clearance_pass_count": 0,
        "missing_support_rejection_count": 0,
        "first_blocker_rejection_count": 0,
        "height_continuity_rejection_count": 0,
        "slope_rejection_count": 0,
        "multi_track_rejection_count": 0,
        "capsule_clearance_rejection_count": 0,
        "connected_component_count": 0,
        "street_block_count": 0,
        "certified_geographic_block_count": 0,
        "junction_count": 0,
        "portal_count": 0,
        "spawn_district_count": 0,
        "certified_directional_lane_length_cm": 0.0,
        "whole_area_recertification_count": 0,
    }


def compute_hashes(document: dict[str, Any]) -> None:
    root_hashes = document["hashes"]
    compatibility_keys = (
        "topology_sha256",
        "georeference_sha256",
        "tileset_sha256",
        "collision_settings_sha256",
    )
    for cell in document["cells"]:
        payload = copy.deepcopy(cell)
        payload.pop("hashes", None)
        content_hash = sha256_json(payload)
        combined_input = {key: root_hashes[key] for key in compatibility_keys}
        combined_input["cell_content_sha256"] = content_hash
        cell["hashes"] = {
            "algorithm": "SHA-256",
            **{key: root_hashes[key] for key in compatibility_keys},
            "cell_content_sha256": content_hash,
            "combined_sha256": sha256_json(combined_input),
        }
    digest_rows = [
        {
            "cell_id": cell["cell_id"],
            "cell_content_sha256": cell["hashes"]["cell_content_sha256"],
            "combined_sha256": cell["hashes"]["combined_sha256"],
        }
        for cell in sorted(document["cells"], key=lambda item: item["cell_id"])
    ]
    content_hash = sha256_json(digest_rows)
    combined_input = {key: root_hashes[key] for key in compatibility_keys}
    combined_input["cell_content_sha256"] = content_hash
    root_hashes["cell_content_sha256"] = content_hash
    root_hashes["combined_sha256"] = sha256_json(combined_input)


def certified_component_coverage(component: dict[str, Any]) -> dict[str, Any]:
    """Return topology-derived promotion metrics for one accepted component.

    Coverage minimums apply to one routable graph, never to the sum of
    disconnected certified islands.  Count unique undirected node pairs for
    block-cycle and junction topology so parallel source records cannot create
    artificial streets or intersections.
    """

    undirected_edges: set[tuple[str, str]] = set()
    neighbors: dict[str, set[str]] = defaultdict(set)
    candidate_ids = []
    for candidate, _result in component["pairs"]:
        first = candidate["from_point_id"]
        second = candidate["to_point_id"]
        edge = tuple(sorted((first, second)))
        undirected_edges.add(edge)
        neighbors[first].add(second)
        neighbors[second].add(first)
        candidate_ids.append(candidate["candidate_id"])

    node_ids = set(component["nodes"])
    return {
        "cell_ids": sorted(component["cells"]),
        "node_count": len(node_ids),
        "undirected_lane_count": len(undirected_edges),
        "directional_lane_length_cm": round(
            float(component["length_cm"]) * 2.0, 4
        ),
        "junction_count": sum(
            len(neighbors.get(node_id, ())) >= 3 for node_id in node_ids
        ),
        "street_block_count": max(
            0,
            len(undirected_edges) - len(node_ids) + 1,
        ),
        "candidate_ids": sorted(candidate_ids),
    }


def select_certified_main_component(
    components: list[dict[str, Any]],
    required_cell_ids: Iterable[str],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Select one six-cell component that independently passes task 3.4.

    Disconnected islands may remain in the working certification evidence, but
    they cannot contribute length, blocks, junctions, districts, or runtime
    lanes to the promoted cache.
    """

    required_cells = sorted(required_cell_ids)
    evaluated = [
        (component, certified_component_coverage(component))
        for component in components
    ]
    qualifying = [
        (component, coverage)
        for component, coverage in evaluated
        if coverage["cell_ids"] == required_cells
        and coverage["directional_lane_length_cm"]
        >= MINIMUM_TRUSTED_UNDIRECTED_LENGTH_CM * 2.0
        and coverage["street_block_count"] >= MINIMUM_CONNECTED_STREET_BLOCKS
        and coverage["junction_count"] >= MINIMUM_CERTIFIED_JUNCTIONS
    ]
    if not qualifying:
        diagnostics = sorted(
            (coverage for _component, coverage in evaluated),
            key=lambda coverage: (
                -len(coverage["cell_ids"]),
                -coverage["directional_lane_length_cm"],
                -coverage["street_block_count"],
                -coverage["junction_count"],
                coverage["candidate_ids"][:1],
            ),
        )[:8]
        compact = [
            {
                key: value
                for key, value in coverage.items()
                if key != "candidate_ids"
            }
            for coverage in diagnostics
        ]
        raise CertificationError(
            "no single trusted certified component independently satisfies "
            "the Central main-graph gate (six cells, 3 km directional, four "
            "connected street blocks, eight junctions); top_components={}"
            .format(json.dumps(compact, sort_keys=True))
        )

    return min(
        qualifying,
        key=lambda item: (
            -item[1]["directional_lane_length_cm"],
            -item[1]["street_block_count"],
            -item[1]["junction_count"],
            item[1]["candidate_ids"],
        ),
    )


def build_certified_document(
    plan: dict[str, Any],
    results: dict[str, Any],
    projected: dict[str, Any],
    source: dict[str, Any],
    runtime_hashes: dict[str, str],
) -> tuple[dict[str, Any], dict[str, Any]]:
    expected_ids = {candidate["candidate_id"] for candidate in plan["candidates"]}
    if set(results) != expected_ids:
        missing = sorted(expected_ids - set(results))
        extra = sorted(set(results) - expected_ids)
        raise CertificationError(
            "cannot finalize unresolved candidates; missing={} extra={}".format(
                missing[:5], extra[:5]
            )
        )
    if any(result.get("status") not in {"accepted", "rejected"} for result in results.values()):
        raise CertificationError("working cache contains unresolved result states")
    trusted_ids = trusted_runtime_result_ids(plan, results)
    components = accepted_components(
        plan,
        results,
        allowed_origins=RUNTIME_ELIGIBLE_TOPOLOGY_ORIGINS,
        allowed_candidate_ids=trusted_ids,
    )
    if not components:
        raise CertificationError("no independently certified trusted OSM/semantic segment")
    main_component, main_coverage = select_certified_main_component(
        components,
        plan["cell_ids"],
    )
    selected_components = [main_component]
    selected_cells = list(main_coverage["cell_ids"])
    selected_pairs = [
        pair for component in selected_components for pair in component["pairs"]
    ]
    selected_nodes = set().union(
        *(set(component["nodes"]) for component in selected_components)
    )
    selected_candidate_ids = {
        candidate["candidate_id"] for candidate, _result in selected_pairs
    }
    component_id_by_point: dict[str, str] = {}
    component_id_by_candidate: dict[str, str] = {}
    component_source_records: dict[str, dict[str, Any]] = {}
    for component in selected_components:
        digest = hashlib.sha256(
            "|".join(
                sorted(candidate["candidate_id"] for candidate, _result in component["pairs"])
            ).encode("utf-8")
        ).hexdigest()[:16]
        component_id = stable_id("certified-component-" + digest)
        component_source_records[component_id] = component
        for node_id in component["nodes"]:
            if node_id in component_id_by_point:
                raise CertificationError("certified components overlap at node " + node_id)
            component_id_by_point[node_id] = component_id
        for candidate, _result in component["pairs"]:
            component_id_by_candidate[candidate["candidate_id"]] = component_id
    node_positions = canonical_node_positions(selected_pairs)
    node_owner = {
        node_id: plan["node_owner"][node_id] for node_id in selected_nodes
    }
    cell_source = {cell["cell_id"]: cell for cell in projected["cells"]}
    # A lateral semantic sample near a partition boundary can physically land
    # in the neighboring cell. Re-home only by the configured XY bounds; this
    # changes storage ownership, never topology or adjacency.
    for node_id, position in node_positions.items():
        containing_cells = sorted(
            cell_id
            for cell_id, cell in cell_source.items()
            if float(cell["world_bounds_cm"]["min"][0]) - 0.01
            <= float(position[0])
            <= float(cell["world_bounds_cm"]["max"][0]) + 0.01
            and float(cell["world_bounds_cm"]["min"][1]) - 0.01
            <= float(position[1])
            <= float(cell["world_bounds_cm"]["max"][1]) + 0.01
        )
        if not containing_cells:
            raise CertificationError("certified node lies outside the six-cell core")
        if node_owner[node_id] not in containing_cells:
            node_owner[node_id] = containing_cells[0]
    selected_cells = sorted(set(node_owner.values()))
    if selected_cells != sorted(plan["cell_ids"]):
        raise CertificationError(
            "trusted certified component union loses a cell after XY ownership"
        )
    cells: dict[str, dict[str, Any]] = {}
    for cell_id in plan["cell_ids"]:
        source_bounds = copy.deepcopy(cell_source[cell_id]["world_bounds_cm"])
        source_bounds["min"][2] = -1.0e7
        source_bounds["max"][2] = 1.0e7
        cells[cell_id] = {
            "schema_version": CERTIFIED_SCHEMA_VERSION,
            "cell_id": cell_id,
            "grid_coordinate": cell_source[cell_id]["grid_coordinate"],
            "world_bounds": source_bounds,
            "source_feature_ids": [],
            "nodes": [],
            "directed_lanes": [],
            "portals": [],
            "hashes": {},
            "evidence": empty_evidence(),
            "certified": True,
        }

    neighbors: dict[str, set[str]] = defaultdict(set)
    cross_cell_nodes: set[str] = set()
    for candidate, _result in selected_pairs:
        neighbors[candidate["from_point_id"]].add(candidate["to_point_id"])
        neighbors[candidate["to_point_id"]].add(candidate["from_point_id"])
        if node_owner[candidate["from_point_id"]] != node_owner[candidate["to_point_id"]]:
            cross_cell_nodes.add(candidate["from_point_id"])
            cross_cell_nodes.add(candidate["to_point_id"])
    nodes_by_id: dict[str, dict[str, Any]] = {}
    for node_id in sorted(selected_nodes):
        owner = node_owner[node_id]
        node_degree = len(neighbors[node_id])
        kind = "junction" if node_degree >= 3 else "endpoint"
        if node_degree < 3 and node_id in cross_cell_nodes:
            kind = "portal"
        node = {
            "node_id": stable_id("node-" + node_id),
            "cell_id": owner,
            "component_id": component_id_by_point[node_id],
            "kind": kind,
            "position": node_positions[node_id],
            "incoming_lane_ids": [],
            "outgoing_lane_ids": [],
        }
        nodes_by_id[node_id] = node
        cells[owner]["nodes"].append(node)

    lanes_by_id: dict[str, dict[str, Any]] = {}
    for candidate, result in selected_pairs:
        forward_id = stable_id("lane-{}-ab".format(candidate["candidate_id"]))
        reverse_id = stable_id("lane-{}-ba".format(candidate["candidate_id"]))
        forward_raw = orient_result_to_candidate(candidate, result)
        reverse_raw = reverse_sample_geometry(forward_raw)
        for lane_id, reverse_lane_id, first_point, second_point, raw_samples in (
            (
                forward_id,
                reverse_id,
                candidate["from_point_id"],
                candidate["to_point_id"],
                forward_raw,
            ),
            (
                reverse_id,
                forward_id,
                candidate["to_point_id"],
                candidate["from_point_id"],
                reverse_raw,
            ),
        ):
            owner = node_owner[first_point]
            samples, length_cm = finalize_lane_samples(
                lane_id,
                raw_samples,
                node_positions[first_point],
                node_positions[second_point],
            )
            lane = {
                "lane_id": lane_id,
                "cell_id": owner,
                "component_id": component_id_by_candidate[candidate["candidate_id"]],
                "source_feature_id": candidate["source_feature_id"],
                "from_node_id": nodes_by_id[first_point]["node_id"],
                "to_node_id": nodes_by_id[second_point]["node_id"],
                "reverse_lane_id": reverse_lane_id,
                "topology_origin": candidate["topology_origin"],
                "pedestrian_class": classification_name(candidate["classification"]),
                "width_cm": float(result["width_cm"]),
                "length_cm": length_cm,
                "certified": True,
                "ground_samples": samples,
            }
            lanes_by_id[lane_id] = lane
            cells[owner]["directed_lanes"].append(lane)
            cells[owner]["source_feature_ids"].append(candidate["source_feature_id"])
            nodes_by_id[first_point]["outgoing_lane_ids"].append(lane_id)
            nodes_by_id[second_point]["incoming_lane_ids"].append(lane_id)

    portals_by_id: dict[str, dict[str, Any]] = {}
    for lane_id, lane in sorted(lanes_by_id.items()):
        from_point = next(
            point_id
            for point_id, node in nodes_by_id.items()
            if node["node_id"] == lane["from_node_id"]
        )
        to_point = next(
            point_id
            for point_id, node in nodes_by_id.items()
            if node["node_id"] == lane["to_node_id"]
        )
        local_cell = node_owner[from_point]
        remote_cell = node_owner[to_point]
        if local_cell == remote_cell:
            continue
        portal_id = stable_id("portal-" + lane_id)
        reverse_portal_id = stable_id("portal-" + lane["reverse_lane_id"])
        portal = {
            "portal_id": portal_id,
            "reverse_portal_id": reverse_portal_id,
            "local_cell_id": local_cell,
            "remote_cell_id": remote_cell,
            "local_node_id": lane["from_node_id"],
            "remote_node_id": lane["to_node_id"],
            "directed_lane_id": lane_id,
            "position": copy.deepcopy(nodes_by_id[from_point]["position"]),
            "certified": True,
        }
        cells[local_cell]["portals"].append(portal)
        portals_by_id[portal_id] = portal

    # Rejected physical candidates remain auditable. Accepted generated
    # connectors are excluded from runtime topology and never mislabeled as a
    # physical failure in certified evidence.
    candidate_by_id = {
        candidate["candidate_id"]: candidate for candidate in plan["candidates"]
    }
    for candidate_id, result in results.items():
        if result["status"] != "rejected":
            continue
        candidate = candidate_by_id[candidate_id]
        reason = result["reason"]
        for owner in (
            candidate.get(
                "from_cell_id",
                plan["node_owner"].get(
                    candidate["from_point_id"], candidate["source_cell_id"]
                ),
            ),
            candidate.get(
                "to_cell_id",
                plan["node_owner"].get(
                    candidate["to_point_id"], candidate["source_cell_id"]
                ),
            ),
        ):
            evidence = cells[owner]["evidence"]
            evidence["candidate_lane_count"] += 1
            evidence["rejected_lane_count"] += 1
            evidence[REJECTION_EVIDENCE_KEYS[reason]] += 1

    for cell in cells.values():
        cell["source_feature_ids"] = sorted(set(cell["source_feature_ids"]))
        cell["nodes"].sort(key=lambda item: item["node_id"])
        cell["directed_lanes"].sort(key=lambda item: item["lane_id"])
        cell["portals"].sort(key=lambda item: item["portal_id"])
        if not cell["nodes"] or not cell["directed_lanes"] or not cell["source_feature_ids"]:
            raise CertificationError(
                "selected component leaves cell {} without runtime topology".format(
                    cell["cell_id"]
                )
            )
        z_values = [node["position"][2] for node in cell["nodes"]]
        cell["world_bounds"]["min"][2] = round(min(z_values) - 100.0, 4)
        cell["world_bounds"]["max"][2] = round(max(z_values) + 100.0, 4)
        evidence = cell["evidence"]
        lane_count = len(cell["directed_lanes"])
        sample_count = sum(
            len(lane["ground_samples"]) for lane in cell["directed_lanes"]
        )
        lane_length = sum(lane["length_cm"] for lane in cell["directed_lanes"])
        evidence["candidate_lane_count"] += lane_count
        evidence["certified_lane_count"] = lane_count
        evidence["coarse_support_check_count"] = evidence["candidate_lane_count"]
        evidence["strict_ground_sample_count"] = sample_count
        for key in (
            "exact_xy_support_pass_count",
            "first_blocker_pass_count",
            "height_continuity_pass_count",
            "slope_pass_count",
            "multi_track_support_pass_count",
            "capsule_clearance_pass_count",
        ):
            evidence[key] = sample_count
        evidence["connected_component_count"] = len(
            {node["component_id"] for node in cell["nodes"]}
        )
        evidence["certified_geographic_block_count"] = 1
        evidence["junction_count"] = sum(
            node["kind"] == "junction" for node in cell["nodes"]
        )
        evidence["portal_count"] = len(cell["portals"])
        evidence["certified_directional_lane_length_cm"] = round(lane_length, 4)

    undirected_edges = {
        tuple(sorted((lane["from_node_id"], lane["to_node_id"])))
        for lane in lanes_by_id.values()
    }
    cycle_rank = max(
        0,
        len(undirected_edges) - len(nodes_by_id) + len(selected_components),
    )
    junction_count = sum(node["kind"] == "junction" for node in nodes_by_id.values())
    if junction_count < MINIMUM_CERTIFIED_JUNCTIONS:
        raise CertificationError("certified component union has fewer than eight junctions")
    if cycle_rank < MINIMUM_CONNECTED_STREET_BLOCKS:
        raise CertificationError(
            "certified main component has fewer than four connected street blocks"
        )

    certified_components = []
    for component_id in sorted(component_source_records):
        source_component = component_source_records[component_id]
        component_node_ids = sorted(
            nodes_by_id[node_id]["node_id"] for node_id in source_component["nodes"]
        )
        component_lanes = sorted(
            (
                lane
                for lane in lanes_by_id.values()
                if lane["component_id"] == component_id
            ),
            key=lambda lane: lane["lane_id"],
        )
        component_undirected_edges = {
            tuple(sorted((lane["from_node_id"], lane["to_node_id"])))
            for lane in component_lanes
        }
        certified_components.append(
            {
                "component_id": component_id,
                "cell_ids": sorted(
                    {node_owner[node_id] for node_id in source_component["nodes"]}
                ),
                "node_ids": component_node_ids,
                "directed_lane_ids": [lane["lane_id"] for lane in component_lanes],
                "topology_origins": sorted(
                    {lane["topology_origin"] for lane in component_lanes}
                ),
                "directional_lane_length_cm": round(
                    sum(lane["length_cm"] for lane in component_lanes), 4
                ),
                "junction_count": sum(
                    nodes_by_id[node_id]["kind"] == "junction"
                    for node_id in source_component["nodes"]
                ),
                "street_block_count": max(
                    0,
                    len(component_undirected_edges) - len(component_node_ids) + 1,
                ),
                "certified": True,
            }
        )

    districts = []
    source_districts = {
        district["district_id"]: district for district in projected["spawn_districts"]
    }
    for cell_id in plan["cell_ids"]:
        cell = cells[cell_id]
        district_id = "district-" + cell_id
        source_district = source_districts[district_id]
        component_lanes: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for lane in cell["directed_lanes"]:
            component_lanes[lane["component_id"]].append(lane)
        district_component_id, district_component_lanes = min(
            component_lanes.items(),
            key=lambda item: (
                -sum(lane["length_cm"] for lane in item[1]),
                -len(item[1]),
                item[0],
            ),
        )
        district_component_lanes.sort(key=lambda lane: lane["lane_id"])
        spawn_lanes = [lane["lane_id"] for lane in district_component_lanes[:8]]
        spawn_nodes = sorted(
            {
                lane["from_node_id"] for lane in district_component_lanes[:8]
            }
        )
        districts.append(
            {
                "district_id": district_id,
                "component_id": district_component_id,
                "cell_ids": [cell_id],
                "spawn_node_ids": spawn_nodes,
                "spawn_lane_ids": spawn_lanes,
                "world_bounds": copy.deepcopy(cell["world_bounds"]),
                "target_population": DISTRICT_POPULATION,
                "selection_weight": float(source_district["selection_weight"]),
                "enabled": True,
            }
        )
        cell["evidence"]["spawn_district_count"] = 1

    root_evidence = empty_evidence()
    sum_keys = set(root_evidence) - {
        "connected_component_count",
        "street_block_count",
        "certified_geographic_block_count",
        "spawn_district_count",
        "whole_area_recertification_count",
    }
    for key in sum_keys:
        root_evidence[key] = sum(cell["evidence"][key] for cell in cells.values())
    root_evidence["connected_component_count"] = len(certified_components)
    root_evidence["street_block_count"] = cycle_rank
    root_evidence["certified_geographic_block_count"] = sum(
        bool(cell["directed_lanes"]) for cell in cells.values()
    )
    root_evidence["junction_count"] = junction_count
    root_evidence["portal_count"] = len(portals_by_id)
    root_evidence["spawn_district_count"] = len(districts)
    root_evidence["whole_area_recertification_count"] = 0

    all_positions = [node["position"] for node in nodes_by_id.values()]
    world_bounds = {
        "min": [round(min(point[axis] for point in all_positions) - 100.0, 4) for axis in range(3)],
        "max": [round(max(point[axis] for point in all_positions) + 100.0, 4) for axis in range(3)],
    }
    compatibility_seed = {
        "topology_sha256": source["hashes"]["topology_sha256"],
        **runtime_hashes,
    }
    build_id = str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            "telecomtwin-central-certified:" + sha256_json(compatibility_seed),
        )
    )
    document = {
        "$schema": "../central_network_certified.schema.json",
        "schema_version": CERTIFIED_SCHEMA_VERSION,
        "network_id": stable_id(projected["network_id"] + "-certified"),
        "build_id": build_id,
        "generator_version": GENERATOR_VERSION,
        "world_bounds": world_bounds,
        "hashes": {
            "algorithm": "SHA-256",
            "topology_sha256": source["hashes"]["topology_sha256"],
            "georeference_sha256": runtime_hashes["georeference_sha256"],
            "tileset_sha256": runtime_hashes["tileset_sha256"],
            "collision_settings_sha256": runtime_hashes[
                "collision_settings_sha256"
            ],
            "cell_content_sha256": "0" * 64,
            "combined_sha256": "0" * 64,
        },
        "source_provenance": {
            "dataset_id": source["dataset_id"],
            "source_path": str(SOURCE_PATH.relative_to(_PROJECT_ROOT)),
            "topology_sha256": source["hashes"]["topology_sha256"],
            "document_sha256": source["hashes"]["document_sha256"],
        },
        "cells": [cells[cell_id] for cell_id in sorted(cells)],
        "components": certified_components,
        "spawn_districts": districts,
        "evidence": root_evidence,
    }
    compute_hashes(document)
    audit = {
        "schema_version": 1,
        "generated_at_utc": utc_timestamp(),
        "network_id": document["network_id"],
        "build_id": document["build_id"],
        "source_component_count": plan["source_component_count"],
        "primary_source_component": plan["primary_component"],
        "review_blocked_feature_ids": plan["review_blocked_feature_ids"],
        "accepted_review_feature_ids": plan["accepted_review_feature_ids"],
        "selection_policy": "single-qualifying-six-cell-certified-main-component-no-connectors",
        "main_component_requirements": {
            "cell_ids": sorted(plan["cell_ids"]),
            "minimum_directional_lane_length_cm": (
                MINIMUM_TRUSTED_UNDIRECTED_LENGTH_CM * 2.0
            ),
            "minimum_connected_street_blocks": MINIMUM_CONNECTED_STREET_BLOCKS,
            "minimum_junctions": MINIMUM_CERTIFIED_JUNCTIONS,
        },
        "selected_certified_component_union": {
            "component_count": len(certified_components),
            "cell_ids": selected_cells,
            "node_count": len(selected_nodes),
            "undirected_lane_count": len(selected_pairs),
            "directional_lane_count": len(lanes_by_id),
            "directional_lane_length_cm": root_evidence[
                "certified_directional_lane_length_cm"
            ],
            "junction_count": junction_count,
            "cycle_rank": cycle_rank,
        },
        "accepted_island_candidate_ids": sorted(
            candidate_id
            for candidate_id, result in results.items()
            if result["status"] == "accepted"
            and candidate_id not in selected_candidate_ids
        ),
        "excluded_accepted_generated_connector_ids": sorted(
            candidate_id
            for candidate_id, result in results.items()
            if result["status"] == "accepted"
            and candidate_by_id[candidate_id].get("topology_origin")
            == "generated-connector"
        ),
        "rejections": [
            {
                "candidate_id": candidate_id,
                "reason": result["reason"],
                "detail": result.get("detail", ""),
            }
            for candidate_id, result in sorted(results.items())
            if result["status"] == "rejected"
        ],
        "hashes": document["hashes"],
    }
    return document, audit


def load_verifier_module() -> Any:
    specification = importlib.util.spec_from_file_location(
        "central_network_cache_verifier", VERIFIER_PATH
    )
    if specification is None or specification.loader is None:
        raise CertificationError("could not load strict cache verifier")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def verify_in_process(document: dict[str, Any]) -> dict[str, Any]:
    verifier = load_verifier_module()
    report = verifier.verify_document(document, read_json(SCHEMA_PATH))
    if not report["overall_passed"]:
        raise CertificationError(
            "strict verifier rejected generated document: {}".format(
                json.dumps(report["issues"][:8], ensure_ascii=False)
            )
        )
    return report


def promote_with_external_verifier(
    document: dict[str, Any],
    audit: dict[str, Any],
    settings: dict[str, Any],
    pending_path: Path = PENDING_PATH,
    final_path: Path = FINAL_PATH,
    audit_path: Path = AUDIT_PATH,
    source_path: Path | None = SOURCE_PATH,
    source_schema_path: Path = SOURCE_SCHEMA_PATH,
) -> dict[str, Any]:
    write_json_atomic(pending_path, document)
    report_path = pending_path.with_suffix(".verification.json")
    command = [
        *resolve_host_python(settings),
        str(VERIFIER_PATH),
        str(pending_path),
        "--schema",
        str(SCHEMA_PATH),
    ]
    if source_path is not None:
        command.extend(
            [
                "--source",
                str(source_path),
                "--source-schema",
                str(source_schema_path),
            ]
        )
    command.extend(["--report", str(report_path)])
    completed = subprocess.run(
        command, capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    if completed.returncode != 0:
        raise CertificationError(
            "strict external verifier failed; final cache was not changed\n{}\n{}".format(
                completed.stdout, completed.stderr
            )
        )
    report = read_json(report_path)
    if not report.get("overall_passed"):
        raise CertificationError("external verifier returned zero without an overall pass")
    os.replace(pending_path, final_path)
    audit["strict_verifier_report"] = str(report_path)
    audit["promoted_at_utc"] = utc_timestamp()
    write_json_atomic(audit_path, audit)
    return report


def resolve_host_python(settings: dict[str, Any]) -> list[str]:
    configured = str(settings.get("host_python", "auto"))
    if configured != "auto":
        return [configured]
    candidates: list[Path] = []
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        candidates.extend(
            sorted(
                (Path(local_app_data) / "Programs" / "Python").glob(
                    "Python*/python.exe"
                ),
                reverse=True,
            )
        )
    for candidate in candidates:
        if candidate.is_file():
            return [str(candidate)]
    python = shutil.which("python")
    if python:
        return [python]
    launcher = shutil.which("py")
    if launcher:
        return [launcher, "-3"]
    raise CertificationError(
        "strict promotion needs a host Python with jsonschema; set host_python explicitly"
    )


def safe_editor_property(value: Any, name: str) -> Any:
    try:
        result = value.get_editor_property(name)
    except Exception:
        return None
    if result is None or isinstance(result, (str, bool, int, float)):
        return result
    try:
        return str(result.name)
    except Exception:
        return str(result)


def runtime_compatibility_hashes(settings: dict[str, Any]) -> dict[str, str]:
    if unreal is None:  # host self-test compatibility
        return {
            "georeference_sha256": hashlib.sha256(b"self-test-georef").hexdigest(),
            "tileset_sha256": hashlib.sha256(b"self-test-tileset").hexdigest(),
            "collision_settings_sha256": hashlib.sha256(
                canonical_json_bytes(settings)
            ).hexdigest(),
        }
    actors = unreal.EditorLevelLibrary.get_all_level_actors()
    georeferences = [
        actor
        for actor in actors
        if "CesiumGeoreference" in actor.get_class().get_name()
    ]
    if len(georeferences) != 1:
        raise CertificationError("expected exactly one CesiumGeoreference")
    georeference = georeferences[0]
    georef_snapshot = {
        "actor_path": object_path(georeference),
        "actor_class": georeference.get_class().get_name(),
        "world_name": unreal.EditorLevelLibrary.get_editor_world().get_name(),
        "properties": {
            name: safe_editor_property(georeference, name)
            for name in (
                "origin_placement",
                "origin_longitude",
                "origin_latitude",
                "origin_height",
                "scale",
                "keep_world_origin_near_camera",
                "maximum_world_origin_distance",
            )
            if safe_editor_property(georeference, name) is not None
        },
    }
    georeference_hash = projected_source_sha256(georef_snapshot)
    tilesets = []
    for actor in actors:
        if "Cesium3DTileset" not in actor.get_class().get_name():
            continue
        tilesets.append(
            {
                "actor_path": object_path(actor),
                "actor_class": actor.get_class().get_name(),
                "properties": {
                    name: safe_editor_property(actor, name)
                    for name in (
                        "url",
                        "ion_asset_id",
                        "maximum_screen_space_error",
                        "create_physics_meshes",
                        "enable_frustum_culling",
                        "enable_fog_culling",
                        "enable_occlusion_culling",
                        "generate_smooth_normals",
                    )
                    if safe_editor_property(actor, name) is not None
                },
            }
        )
    tilesets.sort(key=lambda item: item["actor_path"])
    if not tilesets:
        raise CertificationError("no Cesium3DTileset actor is loaded")
    collision_policy = {
        "trace_mode": "CesiumGltfPrimitiveComponent.line_trace_component",
        "trace_complex": True,
        "global_raw_first_blocker": "nearest_xy_relevant_cesium_component",
        "capsule_clearance": "dense_direct_component_vertical_sweep_lattice",
        "full_ground_evidence_mask": FULL_GROUND_EVIDENCE_MASK,
        "settings": {
            key: settings[key]
            for key in sorted(settings)
            if key not in {"operations_per_tick", "checkpoint_candidate_interval", "streaming_stable_ticks", "streaming_min_wait_ticks", "streaming_timeout_ticks", "max_candidate_streaming_restarts", "host_python", "reset_incompatible_work_cache"}
        },
        "tilesets": tilesets,
    }
    return {
        "georeference_sha256": georeference_hash,
        "tileset_sha256": sha256_json(tilesets),
        "collision_settings_sha256": sha256_json(collision_policy),
    }


class LiveCertificationRunner:
    def __init__(
        self,
        projected: dict[str, Any],
        source: dict[str, Any],
        plan: dict[str, Any],
        settings: dict[str, Any],
        runtime_hashes: dict[str, str],
        work: dict[str, Any],
    ):
        self.projected = projected
        self.source = source
        self.plan = plan
        self.settings = settings
        self.runtime_hashes = runtime_hashes
        self.work = work
        self.world = unreal.EditorLevelLibrary.get_editor_world()
        self.adapter = CesiumProbeAdapter(self.world, settings)
        self.known_nodes = rebuild_known_nodes(
            work["results"], allowed_origins=TRUSTED_RUNTIME_TOPOLOGY_ORIGINS
        )
        self.pending = [
            candidate
            for candidate in plan["candidates"]
            if candidate["candidate_id"] not in work["results"]
        ]
        self.current_candidate: dict[str, Any] | None = None
        self.current_generator: Iterator[dict[str, Any]] | None = None
        self.current_cell: str | None = None
        self.current_cell_bounds: dict[str, Any] | None = None
        self.wait_ticks = 0
        self.stable_ticks = 0
        self.last_signature: str | None = None
        self.handle = None
        self.finished = False
        self.completed_since_save = 0
        self.candidate_streaming_restarts = 0

    def set_cell_camera(self, cell_id: str) -> None:
        cell = next(cell for cell in self.projected["cells"] if cell["cell_id"] == cell_id)
        bounds = cell["world_bounds_cm"]
        self.current_cell_bounds = bounds
        center = [
            (float(bounds["min"][axis]) + float(bounds["max"][axis])) * 0.5
            for axis in range(3)
        ]
        location = unreal.Vector(
            center[0], center[1], center[2] + float(self.settings["camera_height_cm"])
        )
        rotation = unreal.Rotator(
            pitch=float(self.settings["camera_pitch_degrees"]), yaw=0.0, roll=0.0
        )
        unreal.EditorLevelLibrary.set_level_viewport_camera_info(location, rotation)

    def save(self) -> None:
        self.work["updated_at_utc"] = utc_timestamp()
        write_json_atomic(WORK_PATH, self.work)

    def stop(self) -> None:
        if self.handle is not None:
            unreal.unregister_slate_post_tick_callback(self.handle)
            self.handle = None
        self.finished = True

    def finalize(self) -> None:
        document, audit = build_certified_document(
            self.plan,
            self.work["results"],
            self.projected,
            self.source,
            self.runtime_hashes,
        )
        report = promote_with_external_verifier(document, audit, self.settings)
        self.work["complete"] = True
        self.work["promoted_build_id"] = document["build_id"]
        self.work["strict_verifier_summary"] = report["summary"]
        self.save()
        print(
            "CENTRAL_CESIUM_CERTIFICATION="
            + json.dumps(
                {
                    "status": "promoted",
                    "build_id": document["build_id"],
                    "directional_lane_count": document["evidence"][
                        "certified_lane_count"
                    ],
                    "ground_sample_count": document["evidence"][
                        "strict_ground_sample_count"
                    ],
                    "population": sum(
                        district["target_population"]
                        for district in document["spawn_districts"]
                    ),
                    "output": str(FINAL_PATH),
                },
                sort_keys=True,
            )
        )

    def tick(self, _delta_seconds: float) -> None:
        try:
            if self.current_generator is not None:
                signature = self.adapter.refresh_components(self.current_cell_bounds)
                if signature != self.last_signature:
                    # Never combine evidence captured across two streamed tile
                    # sets.  Discard only the in-memory partial candidate and
                    # wait for a fresh stable set; completed checkpoints stay.
                    self.current_candidate = None
                    self.current_generator = None
                    self.wait_ticks = 0
                    self.stable_ticks = 0
                    self.last_signature = signature
                    self.candidate_streaming_restarts += 1
                    if self.candidate_streaming_restarts > int(
                        self.settings["max_candidate_streaming_restarts"]
                    ):
                        raise CertificationError(
                            "Cesium tile set changed too often during one candidate"
                        )
                    return
            if self.current_generator is None:
                if not self.pending:
                    self.stop()
                    self.finalize()
                    return
                candidate = self.pending[0]
                cell_id = candidate["source_cell_id"]
                if self.current_cell != cell_id:
                    self.current_cell = cell_id
                    self.set_cell_camera(cell_id)
                    self.wait_ticks = 0
                    self.stable_ticks = 0
                    self.last_signature = None
                signature = self.adapter.refresh_components(self.current_cell_bounds)
                self.wait_ticks += 1
                if (
                    signature == self.last_signature
                    and self.adapter.signature_component_count > 0
                ):
                    self.stable_ticks += 1
                else:
                    self.last_signature = signature
                    self.stable_ticks = 0
                if self.wait_ticks > int(self.settings["streaming_timeout_ticks"]):
                    raise CertificationError(
                        "Cesium components did not stabilize for {}".format(cell_id)
                    )
                if (
                    self.wait_ticks < int(self.settings["streaming_min_wait_ticks"])
                    or self.stable_ticks < int(self.settings["streaming_stable_ticks"])
                ):
                    return
                self.work["streaming_cells"][cell_id] = {
                    "component_signature_sha256": signature,
                    "component_count": self.adapter.signature_component_count,
                    "stabilized_at_utc": utc_timestamp(),
                }
                # Within the current contiguous cell batch, prefer an edge
                # touching a previously certified node.  This propagates real
                # pavement Z and minimizes independent ellipsoid-height seeds.
                for candidate_index, candidate_option in enumerate(self.pending):
                    if candidate_option["source_cell_id"] != cell_id:
                        break
                    if (
                        candidate_option["from_point_id"] in self.known_nodes
                        or candidate_option["to_point_id"] in self.known_nodes
                    ):
                        if candidate_index:
                            self.pending.insert(0, self.pending.pop(candidate_index))
                        break
                candidate = self.pending[0]
                self.current_candidate = candidate
                # Prefer a direction whose start already has certified Z.
                if (
                    candidate["from_point_id"] not in self.known_nodes
                    and candidate["to_point_id"] in self.known_nodes
                ):
                    self.current_candidate = reverse_candidate(candidate)
                self.current_generator = certify_candidate_steps(
                    self.current_candidate,
                    self.adapter,
                    self.settings,
                    self.known_nodes,
                )

            for _operation in range(int(self.settings["operations_per_tick"])):
                try:
                    next(self.current_generator)
                except StopIteration as finished:
                    result = finished.value
                    candidate_id = self.current_candidate["candidate_id"]
                    self.work["results"][candidate_id] = result
                    if (
                        result["status"] == "accepted"
                        and result.get("topology_origin")
                        in TRUSTED_RUNTIME_TOPOLOGY_ORIGINS
                    ):
                        self.known_nodes[result["from_point_id"]] = result["samples"][0][
                            "center_position"
                        ]
                        self.known_nodes[result["to_point_id"]] = result["samples"][-1][
                            "center_position"
                        ]
                    self.pending.pop(0)
                    self.current_candidate = None
                    self.current_generator = None
                    self.candidate_streaming_restarts = 0
                    self.completed_since_save += 1
                    next_cell = (
                        self.pending[0]["source_cell_id"] if self.pending else None
                    )
                    if (
                        self.completed_since_save
                        >= int(self.settings["checkpoint_candidate_interval"])
                        or next_cell != self.current_cell
                        or not self.pending
                    ):
                        self.save()
                        self.completed_since_save = 0
                        status_counts: dict[str, int] = defaultdict(int)
                        for saved_result in self.work["results"].values():
                            status_counts[saved_result["status"]] += 1
                        print(
                            "CENTRAL_CESIUM_CERTIFICATION="
                            + json.dumps(
                                {
                                    "status": "checkpoint",
                                    "cell_id": self.current_cell,
                                    "resolved": len(self.work["results"]),
                                    "remaining": len(self.pending),
                                    "result_status_counts": dict(status_counts),
                                },
                                sort_keys=True,
                            )
                        )
                    return
        except Exception as error:
            self.stop()
            unreal.log_error("CENTRAL_CESIUM_CERTIFICATION_ERROR {}".format(error))
            raise

    def start(self) -> None:
        self.handle = unreal.register_slate_post_tick_callback(self.tick)
        print(
            "CENTRAL_CESIUM_CERTIFICATION="
            + json.dumps(
                {
                    "status": "running",
                    "resolved": len(self.work["results"]),
                    "remaining": len(self.pending),
                    "work_cache": str(WORK_PATH),
                },
                sort_keys=True,
            )
        )


def start_live() -> None:  # pragma: no cover - executed in the editor
    if unreal is None:
        raise CertificationError("live mode requires Unreal")
    world = unreal.EditorLevelLibrary.get_editor_world()
    if world is None or world.get_name() != EXPECTED_WORLD:
        raise CertificationError(
            "open /Game/Maps/shanghai before Central certification"
        )
    projected = read_json(PROJECTED_SOURCE_PATH)
    source = read_json(SOURCE_PATH)
    corrections = read_json(CORRECTIONS_PATH)
    validate_inputs(projected, source, corrections)
    plan = build_source_plan(projected, corrections)
    if len(plan["primary_component"]["cell_ids"]) != CELL_COUNT:
        raise CertificationError("primary source component does not span all six cells")
    settings = copy.deepcopy(DEFAULT_SETTINGS)
    config_path = SCRIPT_DIR / "central_network_certification.config.json"
    if config_path.exists():
        overrides = read_json(config_path)
        unknown = sorted(set(overrides) - set(settings))
        if unknown:
            raise CertificationError("unknown certification settings: {}".format(unknown))
        settings.update(overrides)
    if abs(float(settings["support_spacing_cm"]) - 10.0) > 1.0e-6:
        raise CertificationError("production support spacing must remain exactly 10 cm")
    runtime_hashes = runtime_compatibility_hashes(settings)
    if runtime_hashes["georeference_sha256"] != projected["hashes"]["georeference_sha256"]:
        raise CertificationError("live georeference differs from projected source")
    work = validate_or_create_work(
        plan,
        PROJECTED_SOURCE_PATH,
        CORRECTIONS_PATH,
        settings,
        runtime_hashes,
    )
    augment_plan_with_generated_connectors(plan, work, settings)
    augment_plan_with_semantic_recovery(plan, work, settings)
    state_name = "_OPEN_MASS_CENTRAL_CESIUM_CERTIFICATION_RUNNER"
    previous = getattr(builtins, state_name, None)
    if previous is not None and not previous.finished:
        raise CertificationError("a Central certification runner is already active")
    runner = LiveCertificationRunner(
        projected, source, plan, settings, runtime_hashes, work
    )
    setattr(builtins, state_name, runner)
    runner.start()


def make_synthetic_source() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    cells = []
    for row in range(2):
        for column in range(3):
            cell_id = "central-r{}-c{}".format(row, column)
            cells.append(
                {
                    "schema_version": 1,
                    "cell_id": cell_id,
                    "grid_coordinate": [column, row],
                    "world_bounds_cm": {
                        "min": [column * (40000.0 / 3.0), row * 15000.0, 0.0],
                        "max": [
                            (column + 1) * (40000.0 / 3.0),
                            (row + 1) * 15000.0,
                            200.0,
                        ],
                    },
                    "source_feature_ids": [],
                }
            )
    cell_by_id = {cell["cell_id"]: cell for cell in cells}

    def cell_for(x: float, y: float) -> str:
        column = min(2, int(x / (40000.0 / 3.0)))
        row = min(1, int(y / 15000.0))
        return "central-r{}-c{}".format(row, column)

    nodes = {
        "grid-{}-{}".format(column, row): [
            5000.0 + column * 10000.0,
            5000.0 + row * 10000.0,
            0.0,
        ]
        for row in range(3)
        for column in range(4)
    }
    edge_nodes = []
    for row in range(3):
        for column in range(3):
            edge_nodes.append(
                ("grid-{}-{}".format(column, row), "grid-{}-{}".format(column + 1, row))
            )
    for row in range(2):
        for column in range(4):
            edge_nodes.append(
                ("grid-{}-{}".format(column, row), "grid-{}-{}".format(column, row + 1))
            )
    features = []
    for index, (first_id, second_id) in enumerate(edge_nodes):
        first = nodes[first_id]
        second = nodes[second_id]
        midpoint = [(first[0] + second[0]) * 0.5, (first[1] + second[1]) * 0.5]
        cell_id = cell_for(*midpoint)
        feature_id = "synthetic-feature-{:02d}".format(index)
        cell_by_id[cell_id]["source_feature_ids"].append(feature_id)
        features.append(
            {
                "feature_id": feature_id,
                "parent_feature_id": feature_id,
                "classification": "sidewalk",
                "cell_id": cell_id,
                "requires_manual_review": False,
                "points": [
                    {
                        "point_id": first_id,
                        "unreal_position_cm": first,
                        "is_cell_boundary": False,
                    },
                    {
                        "point_id": second_id,
                        "unreal_position_cm": second,
                        "is_cell_boundary": False,
                    },
                ],
                "tags": {"highway": "footway"},
            }
        )
    topology_hash = hashlib.sha256(b"synthetic-topology").hexdigest()
    georef_hash = hashlib.sha256(b"self-test-georef").hexdigest()
    projected = {
        "schema_version": 1,
        "network_id": "synthetic-central",
        "cells": cells,
        "features": features,
        "spawn_districts": [
            {
                "district_id": "district-" + cell["cell_id"],
                "cell_ids": [cell["cell_id"]],
                "selection_weight": 1.0,
                "target_population": 50,
            }
            for cell in cells
        ],
        "hashes": {
            "algorithm": "SHA-256",
            "topology_sha256": topology_hash,
            "georeference_sha256": georef_hash,
            "projected_geometry_sha256": hashlib.sha256(b"synthetic-geometry").hexdigest(),
            "combined_sha256": hashlib.sha256(b"synthetic-combined").hexdigest(),
        },
    }
    source = {
        "schema_version": 1,
        "dataset_id": "synthetic-central",
        "hashes": {
            "algorithm": "SHA-256",
            "topology_sha256": topology_hash,
            "document_sha256": hashlib.sha256(b"synthetic-source").hexdigest(),
        },
    }
    corrections = {
        "schema_version": 1,
        "dataset_id": "synthetic-central",
        "corrections": [],
    }
    return projected, source, corrections


def make_flat_result(candidate: dict[str, Any], settings: dict[str, Any]) -> dict[str, Any]:
    width = lane_width(candidate, settings)
    side = width * 0.5 - float(settings["pedestrian_radius_cm"])
    length = candidate["source_length_xy_cm"]
    first = candidate["from_source_position"]
    second = candidate["to_source_position"]
    direction = [(second[0] - first[0]) / length, (second[1] - first[1]) / length]
    right = [-direction[1], direction[0]]
    samples = []
    for distance in station_distances(length, float(settings["support_spacing_cm"])):
        position = interpolate_candidate(candidate, distance)
        z = 102.0
        samples.append(
            {
                "center_position": round_vector([position[0], position[1], z]),
                "left_track_position": round_vector(
                    [position[0] - right[0] * side, position[1] - right[1] * side, z]
                ),
                "right_track_position": round_vector(
                    [position[0] + right[0] * side, position[1] + right[1] * side, z]
                ),
                "surface_normal": [0.0, 0.0, 1.0],
                "supporting_primitive_id": "synthetic-cesium-primitive",
                "evidence_mask": FULL_GROUND_EVIDENCE_MASK,
            }
        )
    return {
        "status": "accepted",
        "candidate_id": candidate["candidate_id"],
        "source_feature_id": candidate["source_feature_id"],
        "classification": candidate["classification"],
        "topology_origin": candidate["topology_origin"],
        "from_point_id": candidate["from_point_id"],
        "to_point_id": candidate["to_point_id"],
        "width_cm": width,
        "coarse_station_count": len(
            station_distances(length, float(settings["coarse_spacing_cm"]))
        ),
        "samples": samples,
    }


class FakeProbeAdapter:
    def __init__(self):
        self.support_calls = 0
        self.capsule_calls = 0

    def trace_support(
        self, x: float, y: float, expected_z: float, *, seed: bool = False
    ) -> dict[str, Any]:
        self.support_calls += 1
        return {
            "ground_position": [x, y, 100.0],
            "surface_normal": [0.0, 0.0, 1.0],
            "supporting_primitive_id": "synthetic-cesium-primitive",
        }

    def capsule_clear(self, _start: list[float], _end: list[float]) -> bool:
        self.capsule_calls += 1
        return True


def build_synthetic_certified_fixture() -> tuple[
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
]:
    """Build the production-threshold synthetic cache used by host tests."""

    settings = copy.deepcopy(DEFAULT_SETTINGS)
    projected, source, corrections = make_synthetic_source()
    validate_inputs(projected, source, corrections)
    plan = build_source_plan(projected, corrections)
    if len(plan["primary_component"]["cell_ids"]) != CELL_COUNT:
        raise CertificationError("synthetic plan lost a cell")
    results = {
        candidate["candidate_id"]: make_flat_result(candidate, settings)
        for candidate in plan["candidates"]
    }
    runtime_hashes = runtime_compatibility_hashes(settings)
    document, audit = build_certified_document(
        plan, results, projected, source, runtime_hashes
    )
    return document, audit, settings, plan


def run_self_test() -> dict[str, Any]:
    document, audit, settings, plan = build_synthetic_certified_fixture()
    projected, _source, corrections = make_synthetic_source()
    # The synthetic source is intentionally not schema-complete, so validate
    # the generated cache without a source mirror; provenance consistency is
    # still checked internally against its root topology hash.
    report = verify_in_process(document)

    short = copy.deepcopy(plan["candidates"][0])
    short["to_source_position"] = [
        short["from_source_position"][0] + 20.0,
        short["from_source_position"][1],
        short["from_source_position"][2],
    ]
    short["source_length_xy_cm"] = 20.0
    fake = FakeProbeAdapter()
    result = exhaust_generator(
        certify_candidate_steps(short, fake, settings, {})
    )
    if result["status"] != "accepted" or len(result["samples"]) != 3:
        raise CertificationError("10 cm incremental sampler self-test failed")
    if fake.support_calls <= len(result["samples"]) * 3 or fake.capsule_calls <= 0:
        raise CertificationError("multi-track/capsule evidence self-test was bypassed")

    manual = copy.deepcopy(projected)
    manual["features"][0]["requires_manual_review"] = True
    blocked_plan = build_source_plan(manual, corrections)
    if manual["features"][0]["feature_id"] not in blocked_plan["review_blocked_feature_ids"]:
        raise CertificationError("manual-review fail-closed self-test failed")

    return {
        "passed": True,
        "directional_lane_count": document["evidence"]["certified_lane_count"],
        "ground_sample_count": document["evidence"]["strict_ground_sample_count"],
        "cell_count": len(document["cells"]),
        "district_count": len(document["spawn_districts"]),
        "population": sum(
            district["target_population"] for district in document["spawn_districts"]
        ),
        "junction_count": document["evidence"]["junction_count"],
        "cycle_rank": document["evidence"]["street_block_count"],
        "verifier_passed_checks": report["summary"]["passed_check_count"],
        "short_sampler_support_calls": fake.support_calls,
        "short_sampler_capsule_calls": fake.capsule_calls,
        "manual_review_blocked": True,
        "audit_selected_cells": audit["selected_certified_component_union"]["cell_ids"],
    }


def current_dry_run() -> dict[str, Any]:
    projected = read_json(PROJECTED_SOURCE_PATH)
    source = read_json(SOURCE_PATH)
    corrections = read_json(CORRECTIONS_PATH)
    validate_inputs(projected, source, corrections)
    plan = build_source_plan(projected, corrections)
    sample_estimate = sum(
        len(
            station_distances(
                candidate["source_length_xy_cm"],
                float(DEFAULT_SETTINGS["support_spacing_cm"]),
            )
        )
        for candidate in plan["candidates"]
    )
    return {
        "status": "dry-run",
        "network_id": plan["network_id"],
        "source_component_count": plan["source_component_count"],
        "primary_component": plan["primary_component"],
        "manual_review_blocked_count": len(plan["review_blocked_feature_ids"]),
        "manual_review_blocked_feature_ids": plan["review_blocked_feature_ids"],
        "accepted_review_feature_ids": plan["accepted_review_feature_ids"],
        "estimated_forward_ground_sample_count": sample_estimate,
        "estimated_directional_ground_sample_count": sample_estimate * 2,
        "support_spacing_cm": DEFAULT_SETTINGS["support_spacing_cm"],
        "would_write_final": False,
    }


def status_report() -> dict[str, Any]:
    report = {
        "work_cache_exists": WORK_PATH.exists(),
        "pending_exists": PENDING_PATH.exists(),
        "final_exists": FINAL_PATH.exists(),
        "audit_exists": AUDIT_PATH.exists(),
    }
    if WORK_PATH.exists():
        work = read_json(WORK_PATH)
        statuses = defaultdict(int)
        for result in work.get("results", {}).values():
            statuses[result.get("status", "unknown")] += 1
        report.update(
            {
                "work_complete": bool(work.get("complete", False)),
                "resolved_candidate_count": len(work.get("results", {})),
                "result_status_counts": dict(statuses),
                "updated_at_utc": work.get("updated_at_utc"),
            }
        )
    return report


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=("dry-run", "status", "self-test"), nargs="?", default="dry-run"
    )
    return parser.parse_args(argv)


def host_main(argv: list[str]) -> int:
    args = parse_args(argv)
    try:
        if args.command == "dry-run":
            result = current_dry_run()
        elif args.command == "status":
            result = status_report()
        else:
            result = run_self_test()
        print("CENTRAL_CESIUM_CERTIFIER=" + json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except Exception as error:
        print(
            "CENTRAL_CESIUM_CERTIFIER_ERROR="
            + json.dumps({"error": repr(error)}, ensure_ascii=False, sort_keys=True),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    if unreal is None:
        raise SystemExit(host_main(sys.argv[1:]))
    start_live()
