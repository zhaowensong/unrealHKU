"""Draw only collision-accepted Central connector candidates and local context.

This transient editor overlay is intentionally quieter than the full network
review.  White lines are generated connectors awaiting an explicit review;
magenta lines are bounded component-connector candidates; green lines are the
nearby formally trusted OSM/semantic lanes.  Nothing is spawned or saved.
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import stat
import sys
from collections import defaultdict, deque
from pathlib import Path

import unreal


OVERLAY_VERSION = "1.1.0"
DURATION_SECONDS = 180.0
Z_OFFSET_CM = 35.0
CONTEXT_DEPTH = 2
SAMPLE_STRIDE = 10
WORK_RELATIVE_PATH = Path(
    "Scripts/OpenMassCrowd/CentralNetwork/Data/central_network_certification_working.json"
)
CORRECTIONS_RELATIVE_PATH = Path(
    "Scripts/OpenMassCrowd/CentralNetwork/central_manual_corrections.json"
)
PROJECTED_RELATIVE_PATH = Path(
    "Scripts/OpenMassCrowd/CentralNetwork/Data/central_pedestrian_source_unreal.json"
)
REPORT_RELATIVE_PATH = Path(
    "Saved/Reports/central_connector_review_overlay_latest.json"
)
FOREST_REPORT_RELATIVE_PATH = Path(
    "Saved/Reports/central_connector_forest_latest.json"
)

COLORS = {
    "trusted-context": unreal.LinearColor(0.0, 1.0, 0.15, 1.0),
    "accepted-generated-review": unreal.LinearColor(1.0, 1.0, 1.0, 1.0),
    "pending-manual-connector": unreal.LinearColor(1.0, 0.0, 0.75, 1.0),
}


def project_root() -> Path:
    return Path(unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_dir()))


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json_atomic(path: Path, document: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    for candidate in (path, temporary):
        if candidate.exists():
            os.chmod(candidate, stat.S_IREAD | stat.S_IWRITE)
    temporary.write_text(
        json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def vector(values: list[float]) -> unreal.Vector:
    return unreal.Vector(
        float(values[0]), float(values[1]), float(values[2]) + Z_OFFSET_CM
    )


def decimated_points(samples: list[dict]) -> list[unreal.Vector]:
    if not samples:
        return []
    indexes = list(range(0, len(samples), SAMPLE_STRIDE))
    if indexes[-1] != len(samples) - 1:
        indexes.append(len(samples) - 1)
    return [vector(samples[index]["center_position"]) for index in indexes]


def draw_polyline(
    world: unreal.World,
    points: list[unreal.Vector],
    color: unreal.LinearColor,
    thickness: float,
) -> int:
    count = 0
    for first, second in zip(points, points[1:]):
        unreal.SystemLibrary.draw_debug_line(
            world, first, second, color, DURATION_SECONDS, thickness
        )
        count += 1
    return count


def load_certifier(root: Path):
    module_dir = root / "Scripts/OpenMassCrowd/CentralNetwork"
    if str(module_dir) not in sys.path:
        sys.path.insert(0, str(module_dir))
    import certify_central_network_with_cesium as certifier

    return importlib.reload(certifier)


def load_recovery(root: Path):
    module_dir = root / "Scripts/OpenMassCrowd/CentralNetwork"
    if str(module_dir) not in sys.path:
        sys.path.insert(0, str(module_dir))
    import recover_central_semantic_detours_with_cesium as recovery

    return importlib.reload(recovery)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--include-pending-manual",
        action="store_true",
        help="also draw only completed manual Cesium connector chains",
    )
    parser.add_argument(
        "--manual-only",
        action="store_true",
        help="hide generated connector probes and focus on completed manual chains",
    )
    parser.add_argument(
        "--source-id",
        help="draw one completed manual connector source for close review",
    )
    parser.add_argument(
        "--candidate-id",
        help="draw one essential generated connector candidate for close review",
    )
    return parser.parse_args()


def focus_viewport(points: list[unreal.Vector], *, close_review: bool = False) -> None:
    if not points:
        return
    minimum_x = min(point.x for point in points)
    maximum_x = max(point.x for point in points)
    minimum_y = min(point.y for point in points)
    maximum_y = max(point.y for point in points)
    center_z = sum(point.z for point in points) / len(points)
    center = unreal.Vector(
        (minimum_x + maximum_x) * 0.5,
        (minimum_y + maximum_y) * 0.5,
        center_z,
    )
    extent = max(maximum_x - minimum_x, maximum_y - minimum_y)
    minimum_distance = 1200.0 if close_review else 5000.0
    camera = center + unreal.Vector(
        0.0,
        -max(minimum_distance, extent * 0.9),
        max(minimum_distance, extent * 0.75),
    )
    rotation = unreal.MathLibrary.find_look_at_rotation(camera, center)
    unreal.get_editor_subsystem(
        unreal.UnrealEditorSubsystem
    ).set_level_viewport_camera_info(camera, rotation)


def main() -> None:
    args = parse_args()
    root = project_root()
    world = unreal.EditorLevelLibrary.get_editor_world()
    if world is None:
        raise RuntimeError("no editor world is loaded")
    unreal.SystemLibrary.flush_persistent_debug_lines(world)
    certifier = load_certifier(root)
    recovery = load_recovery(root)
    projected = load_json(root / PROJECTED_RELATIVE_PATH)
    corrections = load_json(root / CORRECTIONS_RELATIVE_PATH)
    work = load_json(root / WORK_RELATIVE_PATH)
    forest_path = root / FOREST_REPORT_RELATIVE_PATH
    if not forest_path.exists():
        raise RuntimeError(
            "run audit_central_connector_forest.py before drawing review candidates"
        )
    forest = load_json(forest_path)
    essential_generated_ids = {
        item["candidate_id"] for item in forest.get("essential_connectors", [])
    }
    if args.source_id and args.candidate_id:
        raise RuntimeError("--source-id and --candidate-id are mutually exclusive")
    if args.candidate_id and args.candidate_id not in essential_generated_ids:
        raise RuntimeError("requested candidate is not an essential generated connector")
    completed_manual_source_ids = set(
        work.get("semantic_detour_completed_source_ids", [])
    )
    completed_manual_source_ids.update(
        (work.get("semantic_detour_recovery") or {}).get(
            "completed_source_ids", []
        )
    )
    completed_manual_source_ids.update(
        recovery.completed_manual_connector_source_ids(
            work.get("semantic_recovery_candidates", []), work["results"]
        )
    )
    if args.source_id and args.source_id not in completed_manual_source_ids:
        raise RuntimeError("requested source is not a completed manual chain")
    plan = certifier.build_source_plan(projected, corrections)
    plan["candidates"].extend(work.get("generated_connector_candidates", []))
    semantic_candidates = work.get("semantic_recovery_candidates", [])
    certifier._register_semantic_nodes(plan, semantic_candidates)
    plan["candidates"].extend(semantic_candidates)
    candidate_by_id = {
        candidate["candidate_id"]: candidate for candidate in plan["candidates"]
    }

    review_ids: dict[str, str] = {}
    review_nodes: set[str] = set()
    for candidate_id, result in work.get("results", {}).items():
        if result.get("status") != "accepted":
            continue
        candidate = candidate_by_id.get(candidate_id)
        if candidate is None:
            continue
        origin = candidate.get("topology_origin")
        if (
            origin == "generated-connector"
            and candidate_id in essential_generated_ids
            and not args.manual_only
            and (args.candidate_id is None or candidate_id == args.candidate_id)
        ):
            review_ids[candidate_id] = "accepted-generated-review"
        elif (
            origin == "manual-cesium-connector"
            and args.include_pending_manual
            and candidate.get("semantic_source_candidate_id")
            in completed_manual_source_ids
            and (
                args.source_id is None
                or candidate.get("semantic_source_candidate_id") == args.source_id
            )
        ):
            review_ids[candidate_id] = "pending-manual-connector"
        else:
            continue
        review_nodes.add(candidate["from_point_id"])
        review_nodes.add(candidate["to_point_id"])

    trusted_ids = certifier.trusted_runtime_result_ids(plan, work["results"])
    node_candidates: dict[str, set[str]] = defaultdict(set)
    for candidate_id in trusted_ids:
        candidate = candidate_by_id[candidate_id]
        node_candidates[candidate["from_point_id"]].add(candidate_id)
        node_candidates[candidate["to_point_id"]].add(candidate_id)
    context_ids: set[str] = set()
    visited_nodes = set(review_nodes)
    queue = deque((node_id, 0) for node_id in sorted(review_nodes))
    while queue:
        node_id, depth = queue.popleft()
        if depth >= CONTEXT_DEPTH:
            continue
        for candidate_id in sorted(node_candidates.get(node_id, set())):
            if candidate_id in context_ids:
                continue
            context_ids.add(candidate_id)
            candidate = candidate_by_id[candidate_id]
            for neighbor in (
                candidate["from_point_id"],
                candidate["to_point_id"],
            ):
                if neighbor not in visited_nodes:
                    visited_nodes.add(neighbor)
                    queue.append((neighbor, depth + 1))

    all_points: list[unreal.Vector] = []
    review_points: list[unreal.Vector] = []
    segment_counts = defaultdict(int)
    for candidate_id in sorted(context_ids):
        points = decimated_points(work["results"][candidate_id].get("samples", []))
        all_points.extend(points)
        segment_counts["trusted-context"] += draw_polyline(
            world, points, COLORS["trusted-context"], 3.0
        )

    review_candidate_counts = defaultdict(int)
    for candidate_id, category in sorted(review_ids.items()):
        points = decimated_points(work["results"][candidate_id].get("samples", []))
        if len(points) < 2:
            continue
        all_points.extend(points)
        review_points.extend(points)
        review_candidate_counts[category] += 1
        segment_counts[category] += draw_polyline(
            world, points, COLORS[category], 9.0
        )
        for endpoint in (points[0], points[-1]):
            unreal.SystemLibrary.draw_debug_sphere(
                world,
                endpoint,
                35.0,
                8,
                COLORS[category],
                DURATION_SECONDS,
                3.0,
            )

    close_review = bool(args.source_id or args.candidate_id)
    focus_viewport(review_points if close_review else all_points, close_review=close_review)
    report = {
        "schema_version": 1,
        "overlay_version": OVERLAY_VERSION,
        "world": world.get_name(),
        "transient": True,
        "level_saved": False,
        "duration_seconds": DURATION_SECONDS,
        "sample_stride": SAMPLE_STRIDE,
        "context_depth": CONTEXT_DEPTH,
        "included_pending_manual": bool(args.include_pending_manual),
        "manual_only": bool(args.manual_only),
        "selected_source_id": args.source_id,
        "selected_candidate_id": args.candidate_id,
        "completed_manual_source_ids": sorted(completed_manual_source_ids),
        "trusted_context_candidate_count": len(context_ids),
        "review_candidate_counts": dict(sorted(review_candidate_counts.items())),
        "drawn_segment_counts": dict(sorted(segment_counts.items())),
        "review_candidate_ids": sorted(review_ids),
    }
    write_json_atomic(root / REPORT_RELATIVE_PATH, report)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))


try:
    main()
except Exception as error:
    unreal.log_error("CENTRAL_CONNECTOR_REVIEW_OVERLAY_ERROR {}".format(error))
    raise
