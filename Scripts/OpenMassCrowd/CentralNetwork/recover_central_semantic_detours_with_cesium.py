"""Recover rejected OSM pedestrian edges on real Cesium collision surfaces.

The normal certifier deliberately tries only the original line and bounded
parallel registrations.  Photogrammetry around a kerb, stair landing, or a
building corner can require a short curved path.  This tool performs a bounded
local A* search on exact-XY Cesium support, then sends every selected edge back
through the unchanged strict three-track/capsule certifier.

The search never creates a proximity bridge, teleports between elevations, or
relaxes certification thresholds.  Its output remains ``osm-semantic-recovery``
geometry tied to one original OSM edge and is therefore independently auditable.

Run in the editor with PIE stopped::

    python Scripts/OpenMassCrowd/run_unreal_python_via_mcp.py \
      --file Scripts/OpenMassCrowd/CentralNetwork/recover_central_semantic_detours_with_cesium.py \
      --script-arg=--max-sources --script-arg=1

Use ``--self-test`` on host Python for the deterministic lattice test.
"""

from __future__ import annotations

import argparse
import builtins
import copy
import heapq
import importlib.util
import json
import math
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterator

try:
    import unreal
except ImportError:
    unreal = None


SCRIPT_DIR = Path(__file__).resolve().parent
CERTIFIER_PATH = SCRIPT_DIR / "certify_central_network_with_cesium.py"
AUDIT_PATH = SCRIPT_DIR / "Data" / "central_semantic_detour_recovery.audit.json"
CALLBACK_KEY = "_hk_central_semantic_detour_callback"
RUNNER_KEY = "_hk_central_semantic_detour_runner"

GRID_STEP_CM = 50.0
GRID_MARGIN_TIERS_CM = (100.0, 200.0, 300.0)
STREAMING_MINIMUM_SECONDS = 4.0
OPERATIONS_PER_TICK = 64
SEARCH_ALGORITHM_VERSION = 2
MAXIMUM_ROUTE_ATTEMPTS_PER_SOURCE = 12
EDGE_QUANTIZATION_CM = 10.0
BLOCKED_POINT_RADIUS_FACTOR = 0.45
MANUAL_CONNECTOR_ORIGIN = "manual-cesium-connector"
# Central footpaths are often separated by a full carriageway or plaza.  Ten
# metres only reaches same-pavement fragments; 25 metres covers an urban
# crossing while the grid search and per-edge Cesium probes still decide
# whether a continuous pedestrian surface really exists.
COMPONENT_CONNECTOR_MAX_GAP_CM = 2500.0
COMPONENT_CONNECTOR_MAX_VERTICAL_DELTA_CM = 90.0
COMPONENT_CONNECTOR_PAIR_ATTEMPT_LIMIT = 2
COMPONENT_CONNECTOR_ALLOWED_LEVELS = frozenset({"ground-layer-0"})
CYCLE_CONNECTOR_MAX_GAP_CM = 1000.0
CYCLE_CONNECTOR_MAX_VERTICAL_DELTA_CM = 90.0


def load_certifier() -> Any:
    spec = importlib.util.spec_from_file_location(
        "central_cesium_certifier_for_detours", CERTIFIER_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load Central Cesium certifier")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def consume(generator: Iterator[Any]) -> Any:
    """Consume a yielding certifier/search helper and return its final value."""

    while True:
        try:
            next(generator)
        except StopIteration as finished:
            return finished.value


def edge_key(first: list[float], second: list[float]) -> tuple[int, int, int, int]:
    scale = 1.0 / EDGE_QUANTIZATION_CM
    first_xy = (round(float(first[0]) * scale), round(float(first[1]) * scale))
    second_xy = (round(float(second[0]) * scale), round(float(second[1]) * scale))
    return tuple(first_xy + second_xy)  # Direction matters for an approach edge.


def shared_certified_level(
    first_levels: set[str] | None, second_levels: set[str] | None
) -> str | None:
    """Return one stable semantic level shared by two certified endpoints."""

    shared = sorted((first_levels or set()) & (second_levels or set()))
    return shared[0] if shared else None


def completed_manual_connector_source_ids(
    candidates: list[dict[str, Any]], results: dict[str, Any]
) -> set[str]:
    """Reconstruct source completion from immutable per-chain segment data."""

    chains: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        if candidate.get("topology_origin") != MANUAL_CONNECTOR_ORIGIN:
            continue
        chain_id = candidate.get("semantic_chain_id")
        source_id = candidate.get("semantic_source_candidate_id")
        if not isinstance(chain_id, str) or not isinstance(source_id, str):
            continue
        segment_count = int(candidate.get("semantic_segment_count", 0))
        segment_index = int(candidate.get("semantic_segment_index", -1))
        if segment_count <= 0 or not 0 <= segment_index < segment_count:
            continue
        row = chains.setdefault(
            chain_id,
            {
                "source_id": source_id,
                "segment_count": segment_count,
                "accepted_indexes": set(),
            },
        )
        if row["source_id"] != source_id or row["segment_count"] != segment_count:
            raise RuntimeError("manual connector chain metadata is inconsistent")
        if results.get(candidate["candidate_id"], {}).get("status") == "accepted":
            row["accepted_indexes"].add(segment_index)
    return {
        str(row["source_id"])
        for row in chains.values()
        if row["accepted_indexes"] == set(range(int(row["segment_count"])))
    }


def completed_manual_connector_candidate_ids(
    candidates: list[dict[str, Any]], results: dict[str, Any]
) -> set[str]:
    """Return only accepted segments belonging to a fully complete chain."""

    chains: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        if candidate.get("topology_origin") != MANUAL_CONNECTOR_ORIGIN:
            continue
        chain_id = candidate.get("semantic_chain_id")
        source_id = candidate.get("semantic_source_candidate_id")
        if not isinstance(chain_id, str) or not isinstance(source_id, str):
            continue
        segment_count = int(candidate.get("semantic_segment_count", 0))
        segment_index = int(candidate.get("semantic_segment_index", -1))
        if segment_count <= 0 or not 0 <= segment_index < segment_count:
            continue
        row = chains.setdefault(
            chain_id,
            {
                "source_id": source_id,
                "segment_count": segment_count,
                "accepted_by_index": {},
            },
        )
        if row["source_id"] != source_id or row["segment_count"] != segment_count:
            raise RuntimeError("manual connector chain metadata is inconsistent")
        if results.get(candidate["candidate_id"], {}).get("status") == "accepted":
            row["accepted_by_index"][segment_index] = candidate["candidate_id"]
    output = set()
    for row in chains.values():
        expected = set(range(int(row["segment_count"])))
        if set(row["accepted_by_index"]) == expected:
            output.update(row["accepted_by_index"].values())
    return output


def grid_search_steps(
    certifier: Any,
    adapter: Any,
    start_ground: list[float],
    goal_xy: list[float],
    settings: dict[str, Any],
    *,
    grid_step_cm: float = GRID_STEP_CM,
    margin_cm: float,
    blocked_edges: set[tuple[int, int, int, int]] | None = None,
    blocked_points: list[list[float]] | None = None,
) -> Iterator[dict[str, Any]]:
    """Yield once per collision query and return a supported local path."""

    blocked_edges = blocked_edges or set()
    blocked_points = blocked_points or []
    dx = float(goal_xy[0]) - float(start_ground[0])
    dy = float(goal_xy[1]) - float(start_ground[1])
    length = math.hypot(dx, dy)
    if length <= 1.0:
        return [list(start_ground), [float(goal_xy[0]), float(goal_xy[1]), float(start_ground[2])]]
    direction = (dx / length, dy / length)
    normal = (-direction[1], direction[0])
    station_count = max(1, int(math.ceil(length / grid_step_cm)))
    lateral_count = max(0, int(math.floor(margin_cm / grid_step_cm)))
    # Margin applies around the whole endpoint pair, not only sideways.  Real
    # pavements at a building corner may require a short move behind the start
    # or past the goal before turning; clamping station to [0, goal] falsely
    # reports those Cesium-supported routes as disconnected.
    longitudinal_margin_count = lateral_count
    start_key = (0, 0)
    goal_key = (station_count, 0)

    def point_for(key: tuple[int, int]) -> list[float]:
        station, lateral = key
        along = length if station == station_count else station * grid_step_cm
        across = lateral * grid_step_cm
        return [
            float(start_ground[0]) + direction[0] * along + normal[0] * across,
            float(start_ground[1]) + direction[1] * along + normal[1] * across,
        ]

    support: dict[tuple[int, int], list[float]] = {start_key: list(start_ground)}
    parent: dict[tuple[int, int], tuple[int, int]] = {}
    cost: dict[tuple[int, int], float] = {start_key: 0.0}
    queue: list[tuple[float, float, int, int]] = [(length, 0.0, 0, 0)]
    closed: set[tuple[int, int]] = set()
    neighbor_offsets = (
        (-1, -1),
        (-1, 0),
        (-1, 1),
        (0, -1),
        (0, 1),
        (1, -1),
        (1, 0),
        (1, 1),
    )

    while queue:
        _priority, current_cost, station, lateral = heapq.heappop(queue)
        current_key = (station, lateral)
        if current_key in closed or current_cost > cost.get(current_key, math.inf) + 1.0e-6:
            continue
        closed.add(current_key)
        if current_key == goal_key:
            keys = [current_key]
            while keys[-1] != start_key:
                keys.append(parent[keys[-1]])
            keys.reverse()
            return [support[key] for key in keys]

        current_support = support[current_key]
        current_xy = point_for(current_key)
        for station_delta, lateral_delta in neighbor_offsets:
            neighbor_key = (station + station_delta, lateral + lateral_delta)
            if (
                neighbor_key[0] < -longitudinal_margin_count
                or neighbor_key[0] > station_count + longitudinal_margin_count
                or neighbor_key[1] < -lateral_count
                or neighbor_key[1] > lateral_count
                or neighbor_key in closed
            ):
                continue
            neighbor_xy = point_for(neighbor_key)
            if edge_key(current_xy, neighbor_xy) in blocked_edges:
                continue
            if any(
                math.hypot(
                    neighbor_xy[0] - float(blocked[0]),
                    neighbor_xy[1] - float(blocked[1]),
                )
                # The exact failed directed edge is already excluded above.
                # Keep the collision-evidence point local: a radius larger
                # than one grid step can surround the current certified
                # frontier and make every alternate first hop unreachable.
                < max(10.0, grid_step_cm * BLOCKED_POINT_RADIUS_FACTOR)
                for blocked in blocked_points
            ):
                continue
            horizontal = math.hypot(
                neighbor_xy[0] - current_support[0],
                neighbor_xy[1] - current_support[1],
            )
            if horizontal <= 1.0e-6:
                continue
            try:
                hit = adapter.trace_support(
                    neighbor_xy[0], neighbor_xy[1], current_support[2], seed=False
                )
                yield {"phase": "grid-support", "station": neighbor_key[0]}
                neighbor_support = [float(value) for value in hit["ground_position"]]
                certifier.continuity_check(
                    current_support,
                    neighbor_support,
                    settings,
                    expected_spacing_cm=horizontal,
                )
            except certifier.ProbeRejected:
                continue

            dz = abs(neighbor_support[2] - current_support[2])
            lateral_penalty = abs(neighbor_key[1]) * grid_step_cm * 0.04
            tentative = current_cost + horizontal + dz * 0.5 + lateral_penalty
            if tentative + 1.0e-6 >= cost.get(neighbor_key, math.inf):
                continue
            support[neighbor_key] = neighbor_support
            cost[neighbor_key] = tentative
            parent[neighbor_key] = current_key
            remaining = math.hypot(
                float(goal_xy[0]) - neighbor_xy[0],
                float(goal_xy[1]) - neighbor_xy[1],
            )
            heapq.heappush(
                queue,
                (
                    tentative + remaining,
                    tentative,
                    neighbor_key[0],
                    neighbor_key[1],
                ),
            )
    return None


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-sources", type=int, default=1)
    parser.add_argument("--max-evaluated-sources", type=int, default=50)
    parser.add_argument("--source-id")
    parser.add_argument(
        "--mode", choices=("semantic", "components", "cycles"), default="semantic"
    )
    parser.add_argument("--grid-step-cm", type=float, default=GRID_STEP_CM)
    parser.add_argument(
        "--grid-margins-cm",
        default=",".join(str(value) for value in GRID_MARGIN_TIERS_CM),
        help="comma-separated positive lateral search margins",
    )
    parser.add_argument(
        "--max-route-attempts",
        type=int,
        default=MAXIMUM_ROUTE_ATTEMPTS_PER_SOURCE,
    )
    parser.add_argument(
        "--frontier-backoff-cm",
        type=float,
        default=0.0,
        help="resume this far behind the closest certified frontier",
    )
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--reset-generated", action="store_true")
    parser.add_argument("--reset-manual-connectors", action="store_true")
    arguments = parser.parse_args(argv)
    if not 10.0 <= arguments.grid_step_cm <= 100.0:
        parser.error("--grid-step-cm must be between 10 and 100 cm")
    try:
        arguments.grid_margins_cm = tuple(
            float(value.strip())
            for value in arguments.grid_margins_cm.split(",")
            if value.strip()
        )
    except ValueError:
        parser.error("--grid-margins-cm must contain only numbers")
    if (
        not arguments.grid_margins_cm
        or any(value < 0.0 or value > 2000.0 for value in arguments.grid_margins_cm)
    ):
        parser.error("--grid-margins-cm values must be between 0 and 2000 cm")
    if not 1 <= arguments.max_route_attempts <= 64:
        parser.error("--max-route-attempts must be between 1 and 64")
    if not 0.0 <= arguments.frontier_backoff_cm <= 1000.0:
        parser.error("--frontier-backoff-cm must be between 0 and 1000 cm")
    return arguments


def reset_generated_detours() -> dict[str, Any]:
    """Remove only candidates written by this tool, preserving all prior work."""

    certifier = load_certifier()
    work = certifier.read_json(certifier.WORK_PATH)
    stored = list(work.get("semantic_recovery_candidates", []))
    removed_ids = {
        candidate["candidate_id"]
        for candidate in stored
        if candidate.get("tags", {}).get("semantic_recovery")
        == "cesium-grid-detour"
    }
    work["semantic_recovery_candidates"] = [
        candidate
        for candidate in stored
        if candidate.get("candidate_id") not in removed_ids
    ]
    for candidate_id in removed_ids:
        work.get("results", {}).pop(candidate_id, None)
    work.pop("semantic_detour_recovery", None)
    work["updated_at_utc"] = certifier.utc_timestamp()
    certifier.write_json_atomic(certifier.WORK_PATH, work)
    report = {
        "status": "reset",
        "removed_candidate_count": len(removed_ids),
        "remaining_semantic_candidate_count": len(
            work["semantic_recovery_candidates"]
        ),
        "updated_at_utc": certifier.utc_timestamp(),
    }
    certifier.write_json_atomic(AUDIT_PATH, report)
    return report


def reset_manual_connectors() -> dict[str, Any]:
    """Remove only pending manual Cesium component-connector candidates."""

    certifier = load_certifier()
    work = certifier.read_json(certifier.WORK_PATH)
    stored = list(work.get("semantic_recovery_candidates", []))
    removed_ids = {
        candidate["candidate_id"]
        for candidate in stored
        if candidate.get("topology_origin") == MANUAL_CONNECTOR_ORIGIN
    }
    work["semantic_recovery_candidates"] = [
        candidate
        for candidate in stored
        if candidate.get("candidate_id") not in removed_ids
    ]
    for candidate_id in removed_ids:
        work.get("results", {}).pop(candidate_id, None)
    work.pop("semantic_detour_recovery", None)
    work["updated_at_utc"] = certifier.utc_timestamp()
    certifier.write_json_atomic(certifier.WORK_PATH, work)
    report = {
        "status": "manual-connectors-reset",
        "removed_candidate_count": len(removed_ids),
        "remaining_semantic_candidate_count": len(
            work["semantic_recovery_candidates"]
        ),
        "updated_at_utc": certifier.utc_timestamp(),
    }
    certifier.write_json_atomic(AUDIT_PATH, report)
    return report


class DetourRunner:
    def __init__(self, arguments: argparse.Namespace):
        if unreal is None:
            raise RuntimeError("live semantic detour recovery requires Unreal Editor")
        self.arguments = arguments
        self.grid_step_cm = float(arguments.grid_step_cm)
        self.grid_margin_tiers_cm = tuple(arguments.grid_margins_cm)
        self.maximum_route_attempts = int(arguments.max_route_attempts)
        self.frontier_backoff_cm = float(arguments.frontier_backoff_cm)
        if arguments.mode == "components" and arguments.source_id:
            raise RuntimeError("--source-id is supported only in semantic mode")
        self.certifier = load_certifier()
        self.world = unreal.EditorLevelLibrary.get_editor_world()
        if self.world is None or self.world.get_name() != self.certifier.EXPECTED_WORLD:
            raise RuntimeError("open /Game/Maps/shanghai and stop PIE before recovery")

        self.projected = self.certifier.read_json(self.certifier.PROJECTED_SOURCE_PATH)
        self.source = self.certifier.read_json(self.certifier.SOURCE_PATH)
        self.corrections = self.certifier.read_json(self.certifier.CORRECTIONS_PATH)
        self.certifier.validate_inputs(self.projected, self.source, self.corrections)
        self.settings = copy.deepcopy(self.certifier.DEFAULT_SETTINGS)
        self.plan = self.certifier.build_source_plan(self.projected, self.corrections)
        self.work = self.certifier.read_json(self.certifier.WORK_PATH)
        self._restore_stored_candidates()
        expected = {candidate["candidate_id"] for candidate in self.plan["candidates"]}
        if set(self.work.get("results", {})) != expected:
            raise RuntimeError("working cache and restored candidate plan are inconsistent")

        self.original_by_id = {
            candidate["candidate_id"]: candidate
            for candidate in self.plan["candidates"]
            if candidate.get("topology_origin") == "openstreetmap"
        }
        trusted_ids = self.certifier.trusted_runtime_result_ids(
            self.plan, self.work["results"]
        )
        trusted_results = {
            candidate_id: self.work["results"][candidate_id]
            for candidate_id in trusted_ids
        }
        # A short XY gap is not enough evidence that two endpoints belong to
        # the same pedestrian surface.  In particular, transition/step nodes
        # can be centimetres from a ground or deck route while representing a
        # different elevation.  Component connectors may therefore bridge only
        # endpoints that already carry the same independently certified,
        # non-transition semantic level.
        self.accepted_levels = self.certifier._accepted_semantic_levels_by_node(
            self.plan, trusted_results
        )
        self.known_nodes: dict[str, list[float]] = {}
        self._rebuild_known_nodes()
        self.blocked_edges: dict[str, set[tuple[int, int, int, int]]] = defaultdict(set)
        self.blocked_points: dict[str, list[list[float]]] = defaultdict(list)
        self.search_signature = json.dumps(
            {
                "algorithm_version": SEARCH_ALGORITHM_VERSION,
                "mode": arguments.mode,
                "grid_step_cm": self.grid_step_cm,
                "grid_margins_cm": list(self.grid_margin_tiers_cm),
                "max_route_attempts": self.maximum_route_attempts,
                "frontier_backoff_cm": self.frontier_backoff_cm,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        self.session_blocked_sources: set[str] = set()
        self.session_blocked_sources.update(
            str(value)
            for value in self.work.get("semantic_detour_blocked_searches", {}).get(
                self.search_signature, []
            )
        )
        self.component_pair_attempts: dict[str, int] = defaultdict(int)
        for candidate in self.plan["candidates"]:
            if candidate.get("topology_origin") != MANUAL_CONNECTOR_ORIGIN:
                continue
            source_id = candidate.get("semantic_source_candidate_id")
            if isinstance(source_id, str) and source_id:
                # A prior bounded component-pair evaluation is terminal until
                # the operator explicitly resets pending manual connectors.
                self.session_blocked_sources.add(source_id)
        self.source_attempts: dict[str, int] = defaultdict(int)
        for candidate in self.plan["candidates"]:
            source_id = candidate.get("semantic_source_candidate_id")
            if not isinstance(source_id, str) or not source_id:
                continue
            tags = candidate.get("tags", {})
            if tags.get("semantic_recovery") != "cesium-grid-detour":
                continue
            try:
                prior_attempt = int(
                    round(float(tags.get("semantic_lateral_offset_cm", 1000.0)) - 1000.0)
                )
            except (TypeError, ValueError):
                prior_attempt = 0
            self.source_attempts[source_id] = max(
                self.source_attempts[source_id], prior_attempt
            )
        self.completed_source_ids: list[str] = []
        self.all_completed_source_ids = completed_manual_connector_source_ids(
            self.work.get("semantic_recovery_candidates", []),
            self.work["results"],
        )
        self.started_at = time.monotonic()
        self.last_log_at = 0.0
        self.finished = False
        self.stage = "select"
        self.generator: Iterator[Any] | None = None
        self.current_source: dict[str, Any] | None = None
        self.current_start_id: str | None = None
        self.current_goal_id: str | None = None
        self.current_path: list[list[float]] | None = None
        self.current_path_index = 0
        self.current_path_ids: list[str] = []
        self.current_chain_id = ""
        self.current_chain_start_id = ""
        self.current_margin_index = 0
        self.streaming_started_at = 0.0
        self.adapter: Any | None = None
        self.sources_completed_this_run = 0
        self.sources_evaluated_this_run = 0
        self.probed_edges = 0
        self.accepted_edges = 0
        self.rejected_edges = 0

    def _restore_stored_candidates(self) -> None:
        for key in ("semantic_recovery_candidates", "generated_connector_candidates"):
            stored = copy.deepcopy(self.work.get(key, []))
            if key == "semantic_recovery_candidates":
                self.certifier._register_semantic_nodes(self.plan, stored)
            self.plan["candidates"].extend(stored)

    def _rebuild_known_nodes(self) -> None:
        accepted_ids = self._accepted_result_ids(include_pending_manual=True)
        accepted_results = {
            candidate_id: self.work["results"][candidate_id]
            for candidate_id in accepted_ids
        }
        self.known_nodes = self.certifier.rebuild_known_nodes(
            accepted_results,
            allowed_origins=self._allowed_origins(include_pending_manual=True),
        )

    def _allowed_origins(self, *, include_pending_manual: bool) -> set[str]:
        origins = set(self.certifier.TRUSTED_RUNTIME_TOPOLOGY_ORIGINS)
        if include_pending_manual:
            origins.add(MANUAL_CONNECTOR_ORIGIN)
            # Collision-accepted generated connectors remain excluded from the
            # formal runtime topology.  Component mode may nevertheless use
            # them as pending-review planning evidence so it grows the real
            # high-value physical components instead of repeatedly targeting
            # tiny trusted fragments.  Promotion still requires an explicit
            # visual-review decision outside this runner.
            origins.add("generated-connector")
        return origins

    def _accepted_result_ids(self, *, include_pending_manual: bool) -> set[str]:
        allowed_origins = self._allowed_origins(
            include_pending_manual=include_pending_manual
        )
        candidate_by_id = {
            candidate["candidate_id"]: candidate
            for candidate in self.plan["candidates"]
        }
        output = set()
        for candidate_id, result in self.work["results"].items():
            candidate = candidate_by_id.get(candidate_id)
            if (
                candidate is None
                or result.get("status") != "accepted"
                or candidate.get("topology_origin") not in allowed_origins
            ):
                continue
            if (
                candidate.get("topology_origin") == MANUAL_CONNECTOR_ORIGIN
                and not include_pending_manual
                and candidate.get("manual_review_status") != "accepted"
            ):
                continue
            output.add(candidate_id)
        return output

    def _accepted_components(self, *, include_pending_manual: bool) -> list[dict[str, Any]]:
        return self.certifier.accepted_components(
            self.plan,
            self.work["results"],
            allowed_origins=self._allowed_origins(
                include_pending_manual=include_pending_manual
            ),
            allowed_candidate_ids=self._accepted_result_ids(
                include_pending_manual=include_pending_manual
            ),
        )

    def _completed_planning_result_ids(self) -> set[str]:
        """Return trusted lanes plus only fully completed manual chains.

        Search prefixes and generated proximity probes remain useful as local
        collision evidence, but allowing them to contract components causes a
        later successful connector to appear useful without reducing the
        formal runtime component count.  Component selection therefore grows
        the graph that can actually be reviewed and promoted.
        """

        completed_sources = set(
            self.work.get("semantic_detour_completed_source_ids", [])
        )
        completed_sources.update(completed_manual_connector_source_ids(
            self.work.get("semantic_recovery_candidates", []),
            self.work["results"],
        ))
        candidate_by_id = {
            candidate["candidate_id"]: candidate
            for candidate in self.plan["candidates"]
        }
        output = self.certifier.trusted_runtime_result_ids(
            self.plan, self.work["results"]
        )
        output.update(
            candidate_id
            for candidate_id, result in self.work["results"].items()
            if result.get("status") == "accepted"
            and candidate_id in candidate_by_id
            and candidate_by_id[candidate_id].get("topology_origin")
            == MANUAL_CONNECTOR_ORIGIN
            and candidate_by_id[candidate_id].get("manual_review_status")
            != "rejected"
            and candidate_by_id[candidate_id].get(
                "semantic_source_candidate_id"
            )
            in completed_sources
        )
        return output

    def _completed_planning_components(self) -> list[dict[str, Any]]:
        return self.certifier.accepted_components(
            self.plan,
            self.work["results"],
            allowed_origins=set(self.certifier.RUNTIME_ELIGIBLE_TOPOLOGY_ORIGINS),
            allowed_candidate_ids=self._completed_planning_result_ids(),
        )

    def _target_reached(self) -> bool:
        components = self._completed_planning_components()
        return any(
            len(component["cells"]) == self.certifier.CELL_COUNT
            and component["length_cm"]
            >= self.certifier.MINIMUM_TRUSTED_UNDIRECTED_LENGTH_CM
            for component in components
        )

    def _select_semantic_source(self) -> dict[str, Any] | None:
        cut = self.certifier.semantic_recovery_plan(self.plan, self.work["results"])
        candidate_ids = list(cut.get("cut_candidate_ids", []))
        if self.arguments.source_id:
            candidate_ids = [self.arguments.source_id]
        candidates = []
        for candidate_id in candidate_ids:
            source = self.original_by_id.get(candidate_id)
            if (
                source is None
                or candidate_id in self.session_blocked_sources
                or self.work["results"].get(candidate_id, {}).get("status") != "rejected"
            ):
                continue
            known_count = int(source["from_point_id"] in self.known_nodes) + int(
                source["to_point_id"] in self.known_nodes
            )
            if known_count == 0:
                continue
            candidates.append(
                (
                    -known_count,
                    float(source["source_length_xy_cm"]),
                    candidate_id,
                    source,
                )
            )
        if not candidates:
            return None
        candidates.sort(key=lambda item: item[:3])
        return copy.deepcopy(candidates[0][3])

    def _select_component_source(self) -> dict[str, Any] | None:
        components = self._completed_planning_components()
        if len(components) < 2:
            return None
        # Grow the component that already covers the most Central cells first.
        # Coverage is the acceptance bottleneck (six connected districts), so a
        # long two-cell fragment must not outrank a slightly shorter three-cell
        # component.  Length remains the deterministic tie-breaker.
        ranked = sorted(
            enumerate(components),
            key=lambda item: (
                -len(item[1]["cells"]),
                -float(item[1]["length_cm"]),
                sorted(item[1]["nodes"]),
            ),
        )
        main_index = ranked[0][0]
        pair_options = []
        for first_index, first_component in enumerate(components):
            for second_index in range(first_index + 1, len(components)):
                second_component = components[second_index]
                touches_main = main_index in {first_index, second_index}
                first_root = min(first_component["nodes"])
                second_root = min(second_component["nodes"])
                component_pair_key = "|".join(sorted((first_root, second_root)))
                if (
                    self.component_pair_attempts[component_pair_key]
                    >= COMPONENT_CONNECTOR_PAIR_ATTEMPT_LIMIT
                ):
                    continue
                if touches_main:
                    other_component = (
                        second_component
                        if first_index == main_index
                        else first_component
                    )
                    new_cell_count = len(
                        set(other_component["cells"])
                        - set(components[main_index]["cells"])
                    )
                    component_prize = float(other_component["length_cm"])
                else:
                    new_cell_count = len(
                        set(first_component["cells"])
                        | set(second_component["cells"])
                    )
                    component_prize = float(first_component["length_cm"]) + float(
                        second_component["length_cm"]
                    )
                for first_node in sorted(first_component["nodes"]):
                    first = self.known_nodes.get(first_node)
                    if first is None:
                        continue
                    for second_node in sorted(second_component["nodes"]):
                        second = self.known_nodes.get(second_node)
                        if second is None:
                            continue
                        semantic_level_key = shared_certified_level(
                            self.accepted_levels.get(first_node),
                            self.accepted_levels.get(second_node),
                        )
                        if semantic_level_key is None:
                            continue
                        if semantic_level_key not in COMPONENT_CONNECTOR_ALLOWED_LEVELS:
                            continue
                        distance = self.certifier.distance_xy(first, second)
                        vertical = abs(float(first[2]) - float(second[2]))
                        if (
                            distance > COMPONENT_CONNECTOR_MAX_GAP_CM
                            or vertical > COMPONENT_CONNECTOR_MAX_VERTICAL_DELTA_CM
                        ):
                            continue
                        first_id, second_id = sorted((first_node, second_node))
                        source_id = self.certifier.stable_id(
                            "candidate-manual-cesium-connector-{}-{}".format(
                                first_id, second_id
                            )
                        )
                        if source_id in self.session_blocked_sources:
                            continue
                        pair_options.append(
                            (
                                0 if touches_main else 1,
                                distance,
                                -new_cell_count,
                                -component_prize,
                                vertical,
                                semantic_level_key,
                                component_pair_key,
                                source_id,
                                first_node,
                                second_node,
                            )
                        )
        if not pair_options:
            return None
        pair_options.sort()
        (
            _main_rank,
            distance,
            _new_cell_rank,
            _component_prize_rank,
            vertical,
            semantic_level_key,
            component_pair_key,
            source_id,
            first_node,
            second_node,
        ) = pair_options[0]
        first = self.known_nodes[first_node]
        second = self.known_nodes[second_node]
        return {
            "candidate_id": source_id,
            "source_feature_id": source_id.replace("candidate-", "", 1),
            "source_cell_id": self.plan["node_owner"].get(first_node),
            "classification": "manual-pedestrian-link",
            "topology_origin": MANUAL_CONNECTOR_ORIGIN,
            "from_point_id": first_node,
            "to_point_id": second_node,
            "from_source_position": list(first),
            "to_source_position": list(second),
            "source_length_xy_cm": distance,
            "from_cell_id": self.plan["node_owner"].get(first_node),
            "to_cell_id": self.plan["node_owner"].get(second_node),
            "tags": {
                "manual_review_required": "true",
                "component_connector": "true",
                "endpoint_vertical_delta_cm": round(vertical, 4),
                "semantic_level_key": semantic_level_key,
                "component_pair_key": component_pair_key,
            },
            "requires_manual_review": True,
        }

    def _select_cycle_source(self) -> dict[str, Any] | None:
        """Recover a rejected OSM edge whose endpoints are already connected.

        Component connectors grow reach but are bridges and therefore cannot
        create city blocks.  A completed, independently Cesium-certified path
        for a rejected OSM edge inside the main component raises its cycle rank
        by one without inventing an arbitrary shortcut.
        """

        components = self._completed_planning_components()
        if not components:
            return None
        main = sorted(
            components,
            key=lambda component: (
                -len(component["cells"]),
                -float(component["length_cm"]),
                sorted(component["nodes"]),
            ),
        )[0]
        main_nodes = set(main["nodes"])
        options = []
        for source in self.original_by_id.values():
            source_id = source["candidate_id"]
            if (
                source.get("topology_origin") != "openstreetmap"
                or self.work["results"].get(source_id, {}).get("status") != "rejected"
                or source_id in self.session_blocked_sources
                or source_id in self.all_completed_source_ids
                or source["from_point_id"] not in main_nodes
                or source["to_point_id"] not in main_nodes
            ):
                continue
            first = self.known_nodes.get(source["from_point_id"])
            second = self.known_nodes.get(source["to_point_id"])
            if first is None or second is None:
                continue
            semantic_level_key = shared_certified_level(
                self.accepted_levels.get(source["from_point_id"]),
                self.accepted_levels.get(source["to_point_id"]),
            )
            if semantic_level_key not in COMPONENT_CONNECTOR_ALLOWED_LEVELS:
                continue
            distance = self.certifier.distance_xy(first, second)
            vertical = abs(float(first[2]) - float(second[2]))
            if (
                distance > CYCLE_CONNECTOR_MAX_GAP_CM
                or vertical > CYCLE_CONNECTOR_MAX_VERTICAL_DELTA_CM
            ):
                continue
            options.append(
                (
                    distance,
                    vertical,
                    source_id,
                    semantic_level_key,
                    source,
                )
            )
        if not options:
            return None
        options.sort(key=lambda item: item[:3])
        _distance, _vertical, _source_id, semantic_level_key, selected = options[0]
        selected = copy.deepcopy(selected)
        selected.setdefault("tags", {}).update(
            {
                "manual_review_required": "true",
                "cycle_closure": "true",
                "semantic_level_key": semantic_level_key,
            }
        )
        selected["requires_manual_review"] = True
        return selected

    def _select_source(self) -> dict[str, Any] | None:
        if self.arguments.mode == "components":
            return self._select_component_source()
        if self.arguments.mode == "cycles":
            return self._select_cycle_source()
        return self._select_semantic_source()

    def _orient_source(self, source: dict[str, Any]) -> dict[str, Any]:
        from_known = source["from_point_id"] in self.known_nodes
        to_known = source["to_point_id"] in self.known_nodes
        if from_known and not to_known:
            oriented = source
        if to_known and not from_known:
            oriented = self.certifier.reverse_candidate(source)
        elif from_known and to_known:
            oriented = source
        elif not from_known:
            raise RuntimeError("selected source has no certified endpoint")

        # Prior bounded runs may have independently certified a long prefix
        # before a kerb/photogrammetry seam rejected a later edge. Resume from
        # the nearest certified frontier connected to the oriented start;
        # restarting at the OSM endpoint discards valid work and can regenerate
        # an already-used candidate ID without moving the component boundary.
        source_id = oriented["candidate_id"]
        adjacency: dict[str, set[str]] = defaultdict(set)
        for candidate in self.plan["candidates"]:
            if candidate.get("semantic_source_candidate_id") != source_id:
                continue
            result = self.work["results"].get(candidate["candidate_id"], {})
            if result.get("status") != "accepted":
                continue
            first_id = candidate["from_point_id"]
            second_id = candidate["to_point_id"]
            adjacency[first_id].add(second_id)
            adjacency[second_id].add(first_id)
        start_id = oriented["from_point_id"]
        reachable = {start_id}
        queue = [start_id]
        while queue:
            node_id = queue.pop()
            for neighbor_id in adjacency.get(node_id, set()):
                if neighbor_id not in reachable:
                    reachable.add(neighbor_id)
                    queue.append(neighbor_id)
        goal_id = oriented["to_point_id"]
        goal_position = oriented["to_source_position"]
        frontier_options = [
            (
                self.certifier.distance_xy(self.known_nodes[node_id], goal_position),
                node_id,
            )
            for node_id in reachable
            if node_id != goal_id and node_id in self.known_nodes
        ]
        if not frontier_options:
            return oriented
        frontier_options.sort()
        closest_distance = frontier_options[0][0]
        backed_off = [
            option
            for option in frontier_options
            if option[0] >= closest_distance + self.frontier_backoff_cm - 1.0e-6
        ]
        _distance, frontier_id = backed_off[0] if backed_off else frontier_options[-1]
        if frontier_id == start_id:
            return oriented
        resumed = copy.deepcopy(oriented)
        resumed["from_point_id"] = frontier_id
        resumed["from_source_position"] = list(self.known_nodes[frontier_id])
        resumed["from_cell_id"] = self.plan["node_owner"].get(
            frontier_id, oriented.get("from_cell_id")
        )
        resumed["source_cell_id"] = resumed["from_cell_id"]
        resumed["source_length_xy_cm"] = self.certifier.distance_xy(
            resumed["from_source_position"], resumed["to_source_position"]
        )
        resumed.setdefault("tags", {})["semantic_resume_frontier"] = frontier_id
        unreal.log_warning(
            "OPEN_MASS_CROWD_CENTRAL_DETOUR_RESUME source={} frontier={} remaining_xy_cm={:.3f} backoff_cm={:.3f}".format(
                source_id,
                frontier_id,
                resumed["source_length_xy_cm"],
                self.frontier_backoff_cm,
            )
        )
        return resumed

    def _move_camera(self, source: dict[str, Any]) -> None:
        first = source["from_source_position"]
        second = source["to_source_position"]
        center_z = self.known_nodes[source["from_point_id"]][2]
        location = unreal.Vector(
            (float(first[0]) + float(second[0])) * 0.5,
            (float(first[1]) + float(second[1])) * 0.5,
            float(center_z) + float(self.settings["camera_height_cm"]),
        )
        rotation = unreal.Rotator(
            pitch=float(self.settings["camera_pitch_degrees"]), yaw=0.0, roll=0.0
        )
        unreal.EditorLevelLibrary.set_level_viewport_camera_info(location, rotation)

    def _begin_source(self, source: dict[str, Any]) -> None:
        if self.arguments.mode == "components":
            component_pair_key = source.get("tags", {}).get("component_pair_key")
            if isinstance(component_pair_key, str) and component_pair_key:
                self.component_pair_attempts[component_pair_key] += 1
        self.current_source = self._orient_source(source)
        self.current_start_id = self.current_source["from_point_id"]
        self.current_goal_id = self.current_source["to_point_id"]
        self.current_margin_index = 0
        self.source_attempts[source["candidate_id"]] += 1
        self.sources_evaluated_this_run += 1
        self._move_camera(self.current_source)
        self.streaming_started_at = time.monotonic()
        self.adapter = None
        self.stage = "stream"
        unreal.log_warning(
            "OPEN_MASS_CROWD_CENTRAL_DETOUR_STREAM source={} attempt={}".format(
                source["candidate_id"], self.source_attempts[source["candidate_id"]]
            )
        )

    def _begin_search(self) -> None:
        assert self.current_source is not None
        assert self.current_start_id is not None
        assert self.current_goal_id is not None
        assert self.adapter is not None
        start_center = self.known_nodes[self.current_start_id]
        start_ground = [
            float(start_center[0]),
            float(start_center[1]),
            float(start_center[2]) - float(self.settings["lane_height_offset_cm"]),
        ]
        goal_source = self.current_source["to_source_position"]
        margin = self.grid_margin_tiers_cm[self.current_margin_index]
        self.generator = grid_search_steps(
            self.certifier,
            self.adapter,
            start_ground,
            [float(goal_source[0]), float(goal_source[1])],
            self.settings,
            grid_step_cm=self.grid_step_cm,
            margin_cm=margin,
            blocked_edges=self.blocked_edges[self.current_source["candidate_id"]],
            blocked_points=self.blocked_points[self.current_source["candidate_id"]],
        )
        self.stage = "search"

    def _prepare_path(self, path: list[list[float]]) -> None:
        assert self.current_source is not None
        assert self.current_start_id is not None
        assert self.current_goal_id is not None
        attempt = self.source_attempts[self.current_source["candidate_id"]]
        self.current_chain_id = self.certifier.stable_id(
            "semantic-grid-detour-{}-a{:03d}-start-{}".format(
                self.current_source["candidate_id"], attempt, self.current_start_id
            )
        )
        self.current_chain_start_id = self.current_start_id
        self.current_path = path
        self.current_path_index = 0
        self.current_path_ids = [self.current_start_id]
        for index in range(1, len(path) - 1):
            self.current_path_ids.append(
                self.certifier.stable_id(
                    "{}-node-{:03d}".format(self.current_chain_id, index)
                )
            )
        self.current_path_ids.append(self.current_goal_id)
        self._begin_edge_certification()

    def _candidate_for_current_edge(self) -> dict[str, Any]:
        assert self.current_source is not None
        assert self.current_path is not None
        index = self.current_path_index
        first_ground = self.current_path[index]
        second_ground = self.current_path[index + 1]
        first_id = self.current_path_ids[index]
        second_id = self.current_path_ids[index + 1]
        lane_offset = float(self.settings["lane_height_offset_cm"])
        first_center = [first_ground[0], first_ground[1], first_ground[2] + lane_offset]
        second_center = [second_ground[0], second_ground[1], second_ground[2] + lane_offset]
        manual_component_connector = self.arguments.mode in {"components", "cycles"}
        topology_origin = (
            MANUAL_CONNECTOR_ORIGIN
            if manual_component_connector
            else "osm-semantic-recovery"
        )
        if self.arguments.mode == "components":
            recovery_name = "manual-cesium-component-connector"
        elif self.arguments.mode == "cycles":
            recovery_name = "manual-cesium-cycle-closure"
        else:
            recovery_name = "cesium-grid-detour"
        tags = copy.deepcopy(self.current_source.get("tags", {}))
        tags.update(
            {
                "semantic_recovery": recovery_name,
                "semantic_source_candidate_id": self.current_source["candidate_id"],
                "semantic_chain_id": self.current_chain_id,
                "semantic_start_point_id": self.current_chain_start_id,
                "semantic_level_key": self.certifier.semantic_profile(self.current_source)[
                    "level_key"
                ],
                "semantic_lateral_offset_cm": 1000.0
                + float(self.source_attempts[self.current_source["candidate_id"]]),
                "semantic_round": int(self.work.get("semantic_recovery_round_count", 0))
                + 1,
                "semantic_segment_index": index,
                "semantic_segment_count": len(self.current_path) - 1,
                "semantic_grid_step_cm": self.grid_step_cm,
                "semantic_grid_margin_cm": self.grid_margin_tiers_cm[
                    self.current_margin_index
                ],
            }
        )
        from_cell = self.plan["node_owner"].get(
            first_id, self.current_source["source_cell_id"]
        )
        to_cell = self.plan["node_owner"].get(
            second_id, self.current_source["source_cell_id"]
        )
        return {
            "candidate_id": self.certifier.stable_id(
                "candidate-{}-segment-{:03d}".format(self.current_chain_id, index)
            ),
            "source_feature_id": self.current_source["source_feature_id"],
            "source_cell_id": self.current_source["source_cell_id"],
            "classification": self.current_source["classification"],
            "topology_origin": topology_origin,
            "from_point_id": first_id,
            "to_point_id": second_id,
            "from_source_position": self.certifier.round_vector(first_center),
            "to_source_position": self.certifier.round_vector(second_center),
            "source_length_xy_cm": self.certifier.distance_xy(first_center, second_center),
            "from_cell_id": from_cell,
            "to_cell_id": to_cell,
            "tags": tags,
            "requires_manual_review": manual_component_connector,
            "manual_review_status": "pending"
            if manual_component_connector
            else "not-required",
            "semantic_seed_policy": "certified-predecessor-required",
            "semantic_source_candidate_id": self.current_source["candidate_id"],
            "semantic_chain_id": self.current_chain_id,
            "semantic_start_point_id": self.current_chain_start_id,
            "semantic_segment_index": index,
            "semantic_segment_count": len(self.current_path) - 1,
        }

    def _begin_edge_certification(self) -> None:
        assert self.adapter is not None
        candidate = self._candidate_for_current_edge()
        self.pending_candidate = candidate
        self.generator = self.certifier.certify_candidate_steps(
            candidate, self.adapter, self.settings, self.known_nodes
        )
        self.stage = "certify"

    def _record_candidate_result(self, candidate: dict[str, Any], result: dict[str, Any]) -> None:
        candidate_id = candidate["candidate_id"]
        if candidate_id in self.work["results"]:
            raise RuntimeError("detour candidate ID already exists: " + candidate_id)
        self.work.setdefault("semantic_recovery_candidates", []).append(
            copy.deepcopy(candidate)
        )
        self.work["results"][candidate_id] = copy.deepcopy(result)
        self.plan["candidates"].append(copy.deepcopy(candidate))
        self.plan["node_owner"].setdefault(
            candidate["from_point_id"], candidate["from_cell_id"]
        )
        self.plan["node_owner"].setdefault(candidate["to_point_id"], candidate["to_cell_id"])
        self.probed_edges += 1
        if result.get("status") == "accepted":
            self.accepted_edges += 1
            self.known_nodes[candidate["to_point_id"]] = [
                float(value) for value in result["samples"][-1]["center_position"]
            ]
        else:
            self.rejected_edges += 1
        self._save_work()

    def _save_work(self) -> None:
        self.work["updated_at_utc"] = self.certifier.utc_timestamp()
        self.work["semantic_detour_recovery"] = {
            "algorithm": "bounded-exact-xy-cesium-grid-a-star",
            "mode": self.arguments.mode,
            "grid_step_cm": self.grid_step_cm,
            "grid_margin_tiers_cm": list(self.grid_margin_tiers_cm),
            "operations_per_tick": OPERATIONS_PER_TICK,
            "source_attempts": dict(sorted(self.source_attempts.items())),
            "sources_evaluated_this_run": self.sources_evaluated_this_run,
            "completed_source_ids": sorted(set(self.completed_source_ids)),
            "probed_edges": self.probed_edges,
            "accepted_edges": self.accepted_edges,
            "rejected_edges": self.rejected_edges,
            "updated_at_utc": self.certifier.utc_timestamp(),
        }
        self.all_completed_source_ids.update(self.completed_source_ids)
        self.work["semantic_detour_completed_source_ids"] = sorted(
            self.all_completed_source_ids
        )
        blocked = sorted(self.session_blocked_sources - self.all_completed_source_ids)
        blocked_searches = copy.deepcopy(
            self.work.get("semantic_detour_blocked_searches", {})
        )
        blocked_searches[self.search_signature] = blocked
        self.work["semantic_detour_blocked_searches"] = blocked_searches
        # Keep the flat list for human-readable compatibility, but pair it with
        # its exact search signature so a finer grid or wider margin may retry.
        self.work["semantic_detour_blocked_source_ids"] = blocked
        self.work["semantic_detour_blocked_source_signature"] = self.search_signature
        self.certifier.write_json_atomic(self.certifier.WORK_PATH, self.work)

    def _finish(self, status: str, detail: str = "") -> None:
        if self.finished:
            return
        self.finished = True
        self._save_work()
        components = self._completed_planning_components()
        trusted_components = self._accepted_components(include_pending_manual=False)
        report = {
            "status": status,
            "detail": detail,
            "target_reached": self._target_reached(),
            "sources_completed_this_run": self.sources_completed_this_run,
            "sources_evaluated_this_run": self.sources_evaluated_this_run,
            "completed_source_ids": sorted(set(self.completed_source_ids)),
            "all_completed_source_ids": sorted(self.all_completed_source_ids),
            "probed_edges": self.probed_edges,
            "accepted_edges": self.accepted_edges,
            "rejected_edges": self.rejected_edges,
            "trusted_component_count": len(components),
            "formally_trusted_component_count": len(trusted_components),
            "pending_manual_review": self.arguments.mode in {"components", "cycles"},
            "largest_component_cells": len(components[0]["cells"]) if components else 0,
            "largest_component_length_cm": round(components[0]["length_cm"], 3)
            if components
            else 0.0,
            "elapsed_wall_seconds": round(time.monotonic() - self.started_at, 3),
            "work_path": str(self.certifier.WORK_PATH),
            "updated_at_utc": self.certifier.utc_timestamp(),
        }
        self.certifier.write_json_atomic(AUDIT_PATH, report)
        unreal.log_warning(
            "OPEN_MASS_CROWD_CENTRAL_SEMANTIC_DETOUR="
            + json.dumps(report, ensure_ascii=False, sort_keys=True)
        )
        handle = getattr(builtins, CALLBACK_KEY, None)
        if handle is not None:
            try:
                unreal.unregister_slate_post_tick_callback(handle)
            except Exception:
                pass
        setattr(builtins, CALLBACK_KEY, None)

    def _complete_search(self, path: list[list[float]] | None) -> None:
        if path and len(path) >= 2:
            self._prepare_path(path)
            return
        self.current_margin_index += 1
        if self.current_margin_index < len(self.grid_margin_tiers_cm):
            self._begin_search()
            return
        assert self.current_source is not None
        source_id = self.current_source["candidate_id"]
        self.session_blocked_sources.add(source_id)
        self._save_work()
        unreal.log_warning(
            "OPEN_MASS_CROWD_CENTRAL_DETOUR_NO_PATH source={} margins={}".format(
                source_id, list(self.grid_margin_tiers_cm)
            )
        )
        self.stage = "select"

    def _complete_edge(self, result: dict[str, Any]) -> None:
        candidate = self.pending_candidate
        self._record_candidate_result(candidate, result)
        if result.get("status") != "accepted":
            assert self.current_source is not None
            assert self.current_path is not None
            first = self.current_path[self.current_path_index]
            second = self.current_path[self.current_path_index + 1]
            self.blocked_edges[self.current_source["candidate_id"]].add(
                edge_key(first, second)
            )
            evidence = result.get("failure_evidence", {})
            blocked_point = evidence.get("query_xy") or evidence.get("source_position")
            if isinstance(blocked_point, list) and len(blocked_point) >= 2:
                self.blocked_points[self.current_source["candidate_id"]].append(
                    [float(blocked_point[0]), float(blocked_point[1])]
                )
            else:
                self.blocked_points[self.current_source["candidate_id"]].append(
                    [
                        (float(first[0]) + float(second[0])) * 0.5,
                        (float(first[1]) + float(second[1])) * 0.5,
                    ]
                )
            if (
                self.source_attempts[self.current_source["candidate_id"]]
                >= self.maximum_route_attempts
            ):
                self.session_blocked_sources.add(self.current_source["candidate_id"])
                self.stage = "select"
                return
            self.source_attempts[self.current_source["candidate_id"]] += 1
            self.current_margin_index = 0
            self._begin_search()
            return

        # A failed later edge must resume from the last independently certified
        # point, not replay and duplicate the already accepted prefix.
        self.current_start_id = candidate["to_point_id"]
        self.current_path_index += 1
        if self.current_path_index < len(self.current_path) - 1:
            self._begin_edge_certification()
            return
        assert self.current_source is not None
        source_id = self.current_source["candidate_id"]
        self.completed_source_ids.append(source_id)
        self.sources_completed_this_run += 1
        unreal.log_warning(
            "OPEN_MASS_CROWD_CENTRAL_DETOUR_SOURCE_ACCEPTED source={} edges={} completed={}".format(
                source_id, len(self.current_path) - 1, self.sources_completed_this_run
            )
        )
        self._rebuild_known_nodes()
        self.stage = "select"

    def tick(self, _delta_seconds: float) -> None:
        if self.finished:
            return
        try:
            now = time.monotonic()
            if now - self.last_log_at >= 5.0:
                self.last_log_at = now
                unreal.log_warning(
                    "OPEN_MASS_CROWD_CENTRAL_DETOUR_PROGRESS stage={} completed={} accepted_edges={} rejected_edges={}".format(
                        self.stage,
                        self.sources_completed_this_run,
                        self.accepted_edges,
                        self.rejected_edges,
                    )
                )

            if self.stage == "select":
                if self.arguments.mode != "cycles" and self._target_reached():
                    self._finish("target-reached")
                    return
                if self.sources_completed_this_run >= max(1, self.arguments.max_sources):
                    self._finish("source-limit-reached")
                    return
                if self.sources_evaluated_this_run >= max(
                    1, self.arguments.max_evaluated_sources
                ):
                    self._finish("evaluation-limit-reached")
                    return
                source = self._select_source()
                if source is None:
                    self._finish("no-progress", "no cut edge has a certified endpoint")
                    return
                self._begin_source(source)
                return

            if self.stage == "stream":
                if now - self.streaming_started_at < STREAMING_MINIMUM_SECONDS:
                    return
                self.adapter = self.certifier.CesiumProbeAdapter(self.world, self.settings)
                self._begin_search()
                return

            if self.generator is None:
                raise RuntimeError("active stage has no yielding operation")
            for _operation in range(OPERATIONS_PER_TICK):
                try:
                    next(self.generator)
                except StopIteration as finished:
                    self.generator = None
                    if self.stage == "search":
                        self._complete_search(finished.value)
                    elif self.stage == "certify":
                        self._complete_edge(finished.value)
                    else:
                        raise RuntimeError("unexpected completed stage: " + self.stage)
                    break
        except Exception as error:
            unreal.log_error(
                "OPEN_MASS_CROWD_CENTRAL_SEMANTIC_DETOUR_ERROR=" + repr(error)
            )
            self._finish("error", repr(error))


def start_live(arguments: argparse.Namespace) -> None:
    old_handle = getattr(builtins, CALLBACK_KEY, None)
    if old_handle is not None:
        try:
            unreal.unregister_slate_post_tick_callback(old_handle)
        except Exception:
            pass
    runner = DetourRunner(arguments)
    handle = unreal.register_slate_post_tick_callback(runner.tick)
    setattr(builtins, RUNNER_KEY, runner)
    setattr(builtins, CALLBACK_KEY, handle)
    unreal.log_warning(
        "OPEN_MASS_CROWD_CENTRAL_SEMANTIC_DETOUR_STARTED="
        + json.dumps(
            {
                "max_sources": arguments.max_sources,
                "max_evaluated_sources": arguments.max_evaluated_sources,
                "source_id": arguments.source_id,
                "mode": arguments.mode,
                "grid_step_cm": arguments.grid_step_cm,
                "grid_margin_tiers_cm": list(arguments.grid_margins_cm),
                "max_route_attempts": arguments.max_route_attempts,
                "frontier_backoff_cm": arguments.frontier_backoff_cm,
            },
            sort_keys=True,
        )
    )


class FakeAdapter:
    def trace_support(
        self, x: float, y: float, expected_z: float, *, seed: bool = False
    ) -> dict[str, Any]:
        del seed
        if 75.0 <= x <= 125.0 and -25.0 <= y <= 25.0:
            raise FakeProbeRejected("missing_support", "synthetic blocker")
        return {"ground_position": [x, y, expected_z], "surface_normal": [0, 0, 1]}


class FakeProbeRejected(RuntimeError):
    def __init__(self, reason: str, detail: str):
        super().__init__(detail)
        self.reason = reason
        self.detail = detail


class FakeStartPocketAdapter:
    """Synthetic U-shaped kerb that can be exited only behind the start."""

    def trace_support(
        self, x: float, y: float, expected_z: float, *, seed: bool = False
    ) -> dict[str, Any]:
        del seed
        if 0.0 <= x <= 100.0 and abs(y) <= 100.0:
            raise FakeProbeRejected("missing_support", "synthetic start pocket")
        return {"ground_position": [x, y, expected_z], "surface_normal": [0, 0, 1]}


def self_test() -> dict[str, Any]:
    certifier = load_certifier()
    # Make the fake exception participate in the same catch path without
    # weakening the production implementation.
    original = certifier.ProbeRejected
    certifier.ProbeRejected = FakeProbeRejected
    try:
        settings = copy.deepcopy(certifier.DEFAULT_SETTINGS)
        path = consume(
            grid_search_steps(
                certifier,
                FakeAdapter(),
                [0.0, 0.0, 0.0],
                [200.0, 0.0],
                settings,
                margin_cm=100.0,
            )
        )
        pocket_path = consume(
            grid_search_steps(
                certifier,
                FakeStartPocketAdapter(),
                [0.0, 0.0, 0.0],
                [200.0, 0.0],
                settings,
                grid_step_cm=50.0,
                margin_cm=150.0,
            )
        )
    finally:
        certifier.ProbeRejected = original
    if not path or len(path) < 3:
        raise RuntimeError("grid search did not route around the synthetic blocker")
    if not any(abs(point[1]) >= GRID_STEP_CM for point in path):
        raise RuntimeError("grid search crossed the blocked synthetic centerline")
    if not pocket_path or not any(point[0] < 0.0 for point in pocket_path):
        raise RuntimeError("grid search did not use longitudinal start margin")
    if shared_certified_level({"ground-layer-0"}, {"deck-layer-1"}) is not None:
        raise RuntimeError("component connector admitted different semantic levels")
    if (
        shared_certified_level(
            {"ground-layer-0", "deck-layer-1"}, {"ground-layer-0"}
        )
        != "ground-layer-0"
    ):
        raise RuntimeError("component connector rejected a shared certified level")
    if "deck-layer-1" in COMPONENT_CONNECTOR_ALLOWED_LEVELS:
        raise RuntimeError("component connector policy admitted a deck-only bridge")
    return {
        "status": "PASS",
        "path_points": len(path),
        "longitudinal_margin_path_points": len(pocket_path),
        "semantic_level_gate": "PASS",
    }


if __name__ == "__main__":
    args = parse_args(sys.argv[1:])
    if args.self_test:
        print(json.dumps(self_test(), sort_keys=True))
    elif args.reset_generated:
        print(json.dumps(reset_generated_detours(), sort_keys=True))
    elif args.reset_manual_connectors:
        print(json.dumps(reset_manual_connectors(), sort_keys=True))
    elif unreal is None:
        raise SystemExit("Run inside Unreal Editor, or pass --self-test.")
    else:
        start_live(args)
