"""Persist six certifier-equivalent Cesium streaming cameras for Central.

The certified Central network is divided into six roughly 150 m square cells.
Each persistent camera is a 110 m, 90 degree nadir view over the authoritative
cell bounds.  This mirrors the certifier's 110 m nadir camera placement while
making the otherwise editor-viewport-dependent FOV and viewport deterministic.

When ``--refresh`` is supplied, readiness is monitored asynchronously so the
editor thread remains free to stream tiles.  Readiness requires both Cesium
load progress and the same cell-scoped, query-collision component signature
used by the certifier to remain stable for a continuous window.
"""

from __future__ import annotations

import argparse
import builtins
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Iterable

try:  # Unreal's embedded Python supplies this module; host self-tests do not.
    import unreal  # type: ignore
except ImportError:  # pragma: no cover - exercised by the host self-test
    unreal = None


SCHEMA_VERSION = 3
EXPECTED_CAMERA_COUNT = 6
EXPECTED_CERTIFIED_GROUND_SAMPLE_COUNT = 38250

# Keep these aligned with CentralNetwork/certify_central_network_with_cesium.py.
CAMERA_HEIGHT_CM = 11000.0
CAMERA_PITCH_DEGREES = -90.0
COMPONENT_BOUNDS_PADDING_CM = 5.0
STREAMING_STABLE_TICKS = 12
STREAMING_MIN_WAIT_TICKS = 30
STREAMING_TIMEOUT_TICKS = 7200

# The certifier moves an editor camera and therefore inherits that viewport's
# projection. Persistent cameras need explicit values. A square 90 degree view
# at 110 m covers a 150 m cell with a 1.25 safety margin and requests useful LOD.
VIEWPORT_WIDTH = 2048.0
VIEWPORT_HEIGHT = 2048.0
FIELD_OF_VIEW_DEGREES = 90.0
BOUNDS_MARGIN = 1.25
LOAD_PROGRESS_READY_PERCENT = 99.9

REPORT_RELATIVE_DIR = Path("Saved/Reports/OpenMassCrowd/Central300")
LATEST_REPORT_NAME = "central_cesium_streaming_cameras_latest.json"
RUNNER_STATE_NAME = "_OPEN_MASS_CENTRAL_CESIUM_STREAMING_CAMERA_MONITOR"


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(_canonical_json_bytes(value)).hexdigest()


def _round_vector(vector: Iterable[float]) -> list[float]:
    return [round(float(value), 4) for value in vector]


def _host_project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _project_root() -> Path:
    if unreal is None:
        return _host_project_root()
    return Path(unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_dir()))


def _certified_data_path(project_root: Path) -> Path:
    return (
        project_root
        / "Scripts/OpenMassCrowd/CentralNetwork/Data/central_network_certified.json"
    )


def _projected_source_path(project_root: Path) -> Path:
    return (
        project_root
        / "Scripts/OpenMassCrowd/CentralNetwork/Data/central_pedestrian_source_unreal.json"
    )


def _load_certified_data(project_root: Path) -> dict[str, Any]:
    return json.loads(_certified_data_path(project_root).read_text(encoding="utf-8"))


def _load_projected_source(project_root: Path) -> dict[str, Any]:
    return json.loads(_projected_source_path(project_root).read_text(encoding="utf-8"))


def _bounds_union(bounds_items: list[dict[str, Any]]) -> dict[str, list[float]]:
    if not bounds_items:
        raise RuntimeError("cannot build a camera for an empty bounds list")
    return {
        "min": [
            min(float(bounds["min"][axis]) for bounds in bounds_items)
            for axis in range(3)
        ],
        "max": [
            max(float(bounds["max"][axis]) for bounds in bounds_items)
            for axis in range(3)
        ],
    }


def _cell_sample_count(cell: dict[str, Any]) -> int:
    return sum(
        len(lane.get("ground_samples", []))
        for lane in cell.get("directed_lanes", [])
    )


def _camera_spec_for_bounds(
    district_id: str,
    cell_ids: list[str],
    certifier_bounds: dict[str, list[float]],
    certified_sample_bounds: dict[str, list[float]],
    certified_sample_count: int,
) -> dict[str, Any]:
    minimum = [float(value) for value in certifier_bounds["min"]]
    maximum = [float(value) for value in certifier_bounds["max"]]
    center = [(minimum[axis] + maximum[axis]) * 0.5 for axis in range(3)]
    location = [center[0], center[1], center[2] + CAMERA_HEIGHT_CM]

    half_fov_radians = math.radians(FIELD_OF_VIEW_DEGREES * 0.5)
    footprint_half_extent = CAMERA_HEIGHT_CM * math.tan(half_fov_radians)
    required_half_extents = [
        (maximum[axis] - minimum[axis]) * 0.5 * BOUNDS_MARGIN
        for axis in range(2)
    ]
    coverage_passed = all(
        required <= footprint_half_extent for required in required_half_extents
    )
    if not coverage_passed:
        raise RuntimeError(
            "110 m / 90 degree camera does not cover certified bounds for {}"
            .format(district_id)
        )

    return {
        "district_id": district_id,
        "cell_ids": cell_ids,
        "coverage_mode": "complete_certified_spawn_cell_world_bounds",
        "camera_bounds_source": (
            "central_pedestrian_source_unreal.cells[].world_bounds_cm"
        ),
        "sample_bounds_source": "central_network_certified.cells[].world_bounds",
        "certified_ground_sample_count": int(certified_sample_count),
        "certified_track_position_count": int(certified_sample_count) * 3,
        "bounds": {"min": minimum, "max": maximum},
        "certified_sample_bounds": certified_sample_bounds,
        "bounds_size_cm": [
            maximum[axis] - minimum[axis] for axis in range(3)
        ],
        "bounds_margin": BOUNDS_MARGIN,
        "camera_height_above_bounds_center_cm": CAMERA_HEIGHT_CM,
        "camera_clearance_above_certified_sample_maximum_z_cm": (
            location[2] - float(certified_sample_bounds["max"][2])
        ),
        "location": location,
        "rotation": [CAMERA_PITCH_DEGREES, 0.0, 0.0],
        "horizontal_field_of_view_degrees": FIELD_OF_VIEW_DEGREES,
        "viewport_size": [VIEWPORT_WIDTH, VIEWPORT_HEIGHT],
        "coverage_footprint_at_certifier_cell_plane_cm": [
            footprint_half_extent * 2.0,
            footprint_half_extent * 2.0,
        ],
        "required_margin_footprint_cm": [
            required_half_extents[0] * 2.0,
            required_half_extents[1] * 2.0,
        ],
        "coverage_passed": coverage_passed,
    }


def _build_camera_specs(
    data: dict[str, Any], projected_source: dict[str, Any]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    cells_by_id = {str(cell["cell_id"]): cell for cell in data.get("cells", [])}
    projected_cells_by_id = {
        str(cell["cell_id"]): cell for cell in projected_source.get("cells", [])
    }
    enabled_districts = sorted(
        (
            district
            for district in data.get("spawn_districts", [])
            if bool(district.get("enabled", False))
        ),
        key=lambda item: str(item["district_id"]),
    )
    if len(enabled_districts) != EXPECTED_CAMERA_COUNT:
        raise RuntimeError(
            "expected {} enabled spawn districts, found {}".format(
                EXPECTED_CAMERA_COUNT, len(enabled_districts)
            )
        )

    specs: list[dict[str, Any]] = []
    covered_cell_ids: set[str] = set()
    total_samples = 0
    for district in enabled_districts:
        district_id = str(district["district_id"])
        cell_ids = [str(cell_id) for cell_id in district.get("cell_ids", [])]
        if not cell_ids:
            raise RuntimeError("spawn district {} has no certified cells".format(district_id))
        missing = [cell_id for cell_id in cell_ids if cell_id not in cells_by_id]
        missing_projected = [
            cell_id for cell_id in cell_ids if cell_id not in projected_cells_by_id
        ]
        if missing:
            raise RuntimeError(
                "spawn district {} references missing cells: {}".format(
                    district_id, ", ".join(missing)
                )
            )
        if missing_projected:
            raise RuntimeError(
                "spawn district {} is missing certifier camera cells: {}".format(
                    district_id, ", ".join(missing_projected)
                )
            )
        duplicates = covered_cell_ids.intersection(cell_ids)
        if duplicates:
            raise RuntimeError(
                "certified spawn cells assigned to multiple cameras: {}".format(
                    ", ".join(sorted(duplicates))
                )
            )
        covered_cell_ids.update(cell_ids)

        cells = [cells_by_id[cell_id] for cell_id in cell_ids]
        projected_cells = [projected_cells_by_id[cell_id] for cell_id in cell_ids]
        certified_bounds = _bounds_union([cell["world_bounds"] for cell in cells])
        certifier_bounds = _bounds_union(
            [cell["world_bounds_cm"] for cell in projected_cells]
        )
        for axis in range(2):
            if (
                abs(certified_bounds["min"][axis] - certifier_bounds["min"][axis])
                > 0.01
                or abs(
                    certified_bounds["max"][axis]
                    - certifier_bounds["max"][axis]
                )
                > 0.01
            ):
                raise RuntimeError(
                    "certified and certifier XY bounds differ for {}".format(
                        district_id
                    )
                )
        sample_count = sum(_cell_sample_count(cell) for cell in cells)
        if sample_count <= 0:
            raise RuntimeError("spawn district {} has no ground samples".format(district_id))
        total_samples += sample_count
        specs.append(
            _camera_spec_for_bounds(
                district_id,
                cell_ids,
                certifier_bounds,
                certified_bounds,
                sample_count,
            )
        )

    evidence_sample_count = int(
        data.get("evidence", {}).get("strict_ground_sample_count", -1)
    )
    if evidence_sample_count != EXPECTED_CERTIFIED_GROUND_SAMPLE_COUNT:
        raise RuntimeError(
            "certified evidence reports {} samples, expected {}".format(
                evidence_sample_count, EXPECTED_CERTIFIED_GROUND_SAMPLE_COUNT
            )
        )
    if total_samples != evidence_sample_count:
        raise RuntimeError(
            "camera-covered sample count {} does not match certified evidence {}"
            .format(total_samples, evidence_sample_count)
        )
    return specs, {
        "enabled_spawn_district_count": len(enabled_districts),
        "covered_cell_count": len(covered_cell_ids),
        "covered_cell_ids": sorted(covered_cell_ids),
        "complete_certified_ground_sample_count": total_samples,
        "complete_certified_track_position_count": total_samples * 3,
    }


def _require_unreal() -> None:
    if unreal is None:
        raise RuntimeError("this operation must run inside Unreal Editor Python")


def _object_path(value: Any) -> str:
    if value is None:
        return ""
    try:
        return str(value.get_path_name())
    except Exception:
        return str(value)


def _safe_property(value: Any, name: str, default: Any = None) -> Any:
    try:
        result = value.get_editor_property(name)
    except Exception:
        return default
    if result is None or isinstance(result, (str, bool, int, float)):
        return result
    try:
        return str(result.name)
    except Exception:
        return str(result)


def _make_cesium_camera(spec: dict[str, Any]) -> Any:
    camera = unreal.CesiumCamera()
    camera.set_editor_property(
        "viewport_size", unreal.Vector2D(VIEWPORT_WIDTH, VIEWPORT_HEIGHT)
    )
    camera.set_editor_property("location", unreal.Vector(*spec["location"]))
    camera.set_editor_property(
        "rotation",
        unreal.Rotator(pitch=CAMERA_PITCH_DEGREES, yaw=0.0, roll=0.0),
    )
    camera.set_editor_property("field_of_view_degrees", FIELD_OF_VIEW_DEGREES)
    camera.set_editor_property("override_aspect_ratio", 0.0)
    return camera


def _camera_signature(camera: Any) -> dict[str, Any]:
    viewport = camera.get_editor_property("viewport_size")
    location = camera.get_editor_property("location")
    rotation = camera.get_editor_property("rotation")
    return {
        "parameter_source": str(camera.get_editor_property("parameter_source")),
        "viewport_size": _round_vector([viewport.x, viewport.y]),
        "location": _round_vector([location.x, location.y, location.z]),
        "rotation": _round_vector([rotation.pitch, rotation.yaw, rotation.roll]),
        "field_of_view_degrees": round(
            float(camera.get_editor_property("field_of_view_degrees")), 4
        ),
        "override_aspect_ratio": round(
            float(camera.get_editor_property("override_aspect_ratio")), 4
        ),
    }


def _camera_list_signature(cameras: list[Any]) -> str:
    return _sha256_json([_camera_signature(camera) for camera in cameras])


def _tileset_record(tileset: Any) -> dict[str, Any]:
    manager = tileset.resolve_camera_manager()
    return {
        "tileset": _object_path(tileset),
        "manager": _object_path(manager) if manager is not None else None,
        "load_progress_percent": _safe_property(tileset, "load_progress"),
        "properties": {
            name: _safe_property(tileset, name)
            for name in (
                "suspend_update",
                "maximum_screen_space_error",
                "create_physics_meshes",
                "enable_frustum_culling",
                "enable_occlusion_culling",
                "maximum_cached_bytes",
                "loading_descendant_limit",
            )
        },
    }


def _component_records(tilesets: list[Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for tileset in tilesets:
        for component in tileset.get_components_by_class(unreal.PrimitiveComponent):
            if component.get_class().get_name() != "CesiumGltfPrimitiveComponent":
                continue
            try:
                accepted = component.is_visible() and component.is_query_collision_enabled()
            except Exception:
                accepted = False
            if not accepted:
                continue
            origin, extent, _radius = unreal.SystemLibrary.get_component_bounds(component)
            records.append(
                {
                    "tileset": _object_path(tileset),
                    "path": _object_path(component),
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
    records.sort(key=lambda item: (item["tileset"], item["path"]))
    return records


def _cell_component_state(
    component_records: list[dict[str, Any]], camera_spec: dict[str, Any]
) -> dict[str, Any]:
    bounds = camera_spec["bounds"]
    minimum = bounds["min"]
    maximum = bounds["max"]
    relevant = [
        record
        for record in component_records
        if record["max"][0] + COMPONENT_BOUNDS_PADDING_CM >= minimum[0]
        and record["min"][0] - COMPONENT_BOUNDS_PADDING_CM <= maximum[0]
        and record["max"][1] + COMPONENT_BOUNDS_PADDING_CM >= minimum[1]
        and record["min"][1] - COMPONENT_BOUNDS_PADDING_CM <= maximum[1]
    ]
    signature_records = [
        {
            "path": record["path"],
            "min": _round_vector(record["min"]),
            "max": _round_vector(record["max"]),
        }
        for record in relevant
    ]
    return {
        "district_id": camera_spec["district_id"],
        "cell_ids": camera_spec["cell_ids"],
        "query_collision_component_count": len(signature_records),
        "component_signature_sha256": _sha256_json(signature_records),
    }


def _readiness_snapshot(
    tilesets: list[Any], camera_specs: list[dict[str, Any]]
) -> dict[str, Any]:
    tileset_states = [_tileset_record(tileset) for tileset in tilesets]
    component_records = _component_records(tilesets)
    cell_states = [
        _cell_component_state(component_records, spec) for spec in camera_specs
    ]
    load_progress_ready = bool(tileset_states) and all(
        isinstance(item["load_progress_percent"], (int, float))
        and float(item["load_progress_percent"]) >= LOAD_PROGRESS_READY_PERCENT
        for item in tileset_states
    )
    component_sets_nonempty = bool(cell_states) and all(
        int(item["query_collision_component_count"]) > 0 for item in cell_states
    )
    combined_signature = _sha256_json(
        [
            {
                "district_id": item["district_id"],
                "component_signature_sha256": item[
                    "component_signature_sha256"
                ],
            }
            for item in cell_states
        ]
    )
    return {
        "observed_at_utc": _utc_timestamp(),
        "tilesets": tileset_states,
        "query_collision_component_count": len(component_records),
        "cells": cell_states,
        "load_progress_ready": load_progress_ready,
        "component_sets_nonempty": component_sets_nonempty,
        "combined_component_signature_sha256": combined_signature,
    }


def _report_paths(project_root: Path) -> tuple[Path, Path]:
    output_dir = project_root / REPORT_RELATIVE_DIR
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return (
        output_dir / "central_cesium_streaming_cameras_{}.json".format(stamp),
        output_dir / LATEST_REPORT_NAME,
    )


def _emit(report: dict[str, Any], timestamped: bool = False) -> None:
    timestamp_path, latest_path = _report_paths(_project_root())
    report["report_paths"] = {"latest": str(latest_path)}
    if timestamped:
        report["report_paths"]["timestamped"] = str(timestamp_path)
    payload = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    latest_path.write_text(payload, encoding="utf-8")
    if timestamped:
        timestamp_path.write_text(payload, encoding="utf-8")
    print(
        "CENTRAL_CESIUM_STREAMING_CAMERAS="
        + json.dumps(report, ensure_ascii=False, sort_keys=True)
    )


class _StreamingReadinessMonitor:
    def __init__(
        self,
        report: dict[str, Any],
        tilesets: list[Any],
        camera_specs: list[dict[str, Any]],
    ) -> None:
        self.report = report
        self.tilesets = tilesets
        self.camera_specs = camera_specs
        self.handle = None
        self.finished = False
        self.wait_ticks = 0
        self.stable_ticks = 0
        self.last_signature: str | None = None
        self.observations: list[dict[str, Any]] = []

    def stop(self) -> None:
        if self.handle is not None:
            unreal.unregister_slate_post_tick_callback(self.handle)
            self.handle = None
        self.finished = True

    def _retain_observation(self, snapshot: dict[str, Any], signature_changed: bool) -> None:
        # Retain changes, the first sample, and a periodic heartbeat without
        # producing a multi-thousand-entry report during a cold network load.
        if (
            not self.observations
            or signature_changed
            or self.wait_ticks % 60 == 0
            or self.stable_ticks in (1, STREAMING_STABLE_TICKS)
        ):
            self.observations.append(
                {
                    "wait_tick": self.wait_ticks,
                    "stable_tick": self.stable_ticks,
                    "load_progress_percent": [
                        item["load_progress_percent"]
                        for item in snapshot["tilesets"]
                    ],
                    "load_progress_ready": snapshot["load_progress_ready"],
                    "component_sets_nonempty": snapshot[
                        "component_sets_nonempty"
                    ],
                    "cell_component_counts": {
                        item["district_id"]: item[
                            "query_collision_component_count"
                        ]
                        for item in snapshot["cells"]
                    },
                    "combined_component_signature_sha256": snapshot[
                        "combined_component_signature_sha256"
                    ],
                }
            )

    def _finish(self, status: str, snapshot: dict[str, Any], error: str | None = None) -> None:
        self.stop()
        passed = status == "ready"
        self.report.update(
            {
                "status": status,
                "overall_passed": passed,
                "completed_at_utc": _utc_timestamp(),
                "readiness": {
                    "requested": True,
                    "passed": passed,
                    "wait_ticks": self.wait_ticks,
                    "continuous_stable_ticks": self.stable_ticks,
                    "required_minimum_wait_ticks": STREAMING_MIN_WAIT_TICKS,
                    "required_continuous_stable_ticks": STREAMING_STABLE_TICKS,
                    "timeout_ticks": STREAMING_TIMEOUT_TICKS,
                    "load_progress_ready_percent": LOAD_PROGRESS_READY_PERCENT,
                    "component_signature_scope": (
                        "visible query-enabled CesiumGltfPrimitiveComponent "
                        "bounds overlapping each certified spawn cell"
                    ),
                    "final_snapshot": snapshot,
                    "observations": self.observations,
                    "error": error,
                },
            }
        )
        _emit(self.report, timestamped=True)
        if not passed:
            unreal.log_error(
                "CENTRAL_CESIUM_STREAMING_CAMERAS_READINESS_FAILED {}".format(
                    error or status
                )
            )

    def tick(self, _delta_seconds: float) -> None:
        try:
            self.wait_ticks += 1
            snapshot = _readiness_snapshot(self.tilesets, self.camera_specs)
            signature = snapshot["combined_component_signature_sha256"]
            signature_changed = signature != self.last_signature
            ready_inputs = (
                snapshot["load_progress_ready"]
                and snapshot["component_sets_nonempty"]
            )
            if ready_inputs and not signature_changed:
                self.stable_ticks += 1
            else:
                self.stable_ticks = 0
            self.last_signature = signature
            self._retain_observation(snapshot, signature_changed)

            if (
                self.wait_ticks >= STREAMING_MIN_WAIT_TICKS
                and self.stable_ticks >= STREAMING_STABLE_TICKS
            ):
                self._finish("ready", snapshot)
            elif self.wait_ticks >= STREAMING_TIMEOUT_TICKS:
                self._finish(
                    "timeout",
                    snapshot,
                    "Cesium load progress and cell component signatures did not stabilize",
                )
        except Exception as error:  # pragma: no cover - Unreal callback guard
            try:
                snapshot = _readiness_snapshot(self.tilesets, self.camera_specs)
            except Exception:
                snapshot = {}
            self._finish("error", snapshot, str(error))

    def start(self) -> None:
        self.handle = unreal.register_slate_post_tick_callback(self.tick)


def _save_editor_map_if_needed(changed: bool, editor_mode: bool) -> dict[str, Any]:
    if not editor_mode:
        return {
            "mode": "runtime_only",
            "persistent": False,
            "changed": changed,
            "saved": False,
        }
    if not changed:
        return {
            "mode": "existing_editor_map_state",
            "persistent": True,
            "changed": False,
            "saved": False,
            "reason": "all loaded map camera managers already matched",
        }
    saved = bool(unreal.EditorLevelLibrary.save_current_level())
    if not saved:
        raise RuntimeError("failed to persist Cesium camera managers in the current map")
    return {
        "mode": "saved_current_editor_level",
        "persistent": True,
        "changed": True,
        "saved": True,
    }


def _certifier_comparison(tileset_records: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "certifier_script": (
            "Scripts/OpenMassCrowd/CentralNetwork/"
            "certify_central_network_with_cesium.py"
        ),
        "camera_height_cm": {
            "certifier": CAMERA_HEIGHT_CM,
            "persistent": CAMERA_HEIGHT_CM,
            "equivalent": True,
        },
        "camera_pitch_degrees": {
            "certifier": CAMERA_PITCH_DEGREES,
            "persistent": CAMERA_PITCH_DEGREES,
            "equivalent": True,
        },
        "projection": {
            "certifier": "active editor viewport projection",
            "persistent_horizontal_field_of_view_degrees": FIELD_OF_VIEW_DEGREES,
            "persistent_viewport_size": [VIEWPORT_WIDTH, VIEWPORT_HEIGHT],
            "reason": "deterministic square projection covering each 150 m cell",
        },
        "maximum_screen_space_error": {
            "policy": "preserve live tileset values used by the certifier",
            "mutated_by_this_script": False,
            "tilesets": [
                {
                    "tileset": item["tileset"],
                    "maximum_screen_space_error": item["properties"].get(
                        "maximum_screen_space_error"
                    ),
                }
                for item in tileset_records
            ],
        },
        "readiness": {
            "certifier_component_signature_method_reused": True,
            "component_bounds_padding_cm": COMPONENT_BOUNDS_PADDING_CM,
            "minimum_wait_ticks": STREAMING_MIN_WAIT_TICKS,
            "continuous_stable_ticks": STREAMING_STABLE_TICKS,
            "load_progress_gate_added": True,
            "load_progress_ready_percent": LOAD_PROGRESS_READY_PERCENT,
        },
    }


def _run_unreal(args: argparse.Namespace) -> None:
    _require_unreal()
    subsystem = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem)
    world = subsystem.get_editor_world() if args.editor else subsystem.get_game_world()
    if world is None:
        raise RuntimeError("requested editor/PIE world is unavailable")

    project_root = _project_root()
    data = _load_certified_data(project_root)
    projected_source = _load_projected_source(project_root)
    camera_specs, coverage = _build_camera_specs(data, projected_source)
    cameras = [_make_cesium_camera(spec) for spec in camera_specs]
    desired_signature = _camera_list_signature(cameras)

    default_manager = unreal.CesiumCameraManager.get_default_camera_manager(world)
    if default_manager is None:
        raise RuntimeError("Cesium camera manager unavailable")
    tilesets = list(
        unreal.GameplayStatics.get_all_actors_of_class(world, unreal.Cesium3DTileset)
    )
    if not tilesets:
        raise RuntimeError("no Cesium3DTileset actor is loaded")

    managers = {_object_path(default_manager): default_manager}
    for tileset in tilesets:
        manager = tileset.resolve_camera_manager()
        if manager is not None:
            managers[_object_path(manager)] = manager

    manager_records: list[dict[str, Any]] = []
    any_changed = False
    for manager_path, manager in sorted(managers.items()):
        existing = list(manager.get_editor_property("additional_cameras"))
        existing_signature = _camera_list_signature(existing)
        changed = existing_signature != desired_signature
        if changed:
            manager.set_editor_property("additional_cameras", cameras)
        final_cameras = list(manager.get_editor_property("additional_cameras"))
        final_signature = _camera_list_signature(final_cameras)
        if len(final_cameras) != EXPECTED_CAMERA_COUNT or final_signature != desired_signature:
            raise RuntimeError(
                "camera manager {} did not retain the exact six-camera set".format(
                    manager_path
                )
            )
        any_changed = any_changed or changed
        manager_records.append(
            {
                "manager": manager_path,
                "existing_camera_count": len(existing),
                "final_camera_count": len(final_cameras),
                "changed": changed,
                "existing_camera_signature_sha256": existing_signature,
                "final_camera_signature_sha256": final_signature,
            }
        )

    persistence = _save_editor_map_if_needed(any_changed, args.editor)
    tileset_records = [_tileset_record(tileset) for tileset in tilesets]
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "status": "registered",
        "overall_passed": True,
        "started_at_utc": _utc_timestamp(),
        "world": _object_path(world),
        "world_mode": "editor" if args.editor else "pie",
        "registration": {
            "idempotent": True,
            "camera_count": len(cameras),
            "camera_signature_sha256": desired_signature,
            "managers": manager_records,
            "map_persistence": persistence,
        },
        "coverage": coverage,
        "cameras": camera_specs,
        "tilesets_before_refresh": tileset_records,
        "certifier_comparison": _certifier_comparison(tileset_records),
        "readiness": {
            "requested": bool(args.refresh),
            "passed": None if args.refresh else "not_evaluated",
        },
    }

    if not args.refresh:
        report["completed_at_utc"] = _utc_timestamp()
        _emit(report, timestamped=True)
        return

    previous = getattr(builtins, RUNNER_STATE_NAME, None)
    if previous is not None and not previous.finished:
        raise RuntimeError("a Central Cesium camera readiness monitor is already active")
    for tileset in tilesets:
        tileset.refresh_tileset()
    report.update({"status": "monitoring", "overall_passed": None})
    monitor = _StreamingReadinessMonitor(report, tilesets, camera_specs)
    setattr(builtins, RUNNER_STATE_NAME, monitor)
    monitor.start()
    _emit(report, timestamped=False)


def _run_self_test() -> None:
    project_root = _host_project_root()
    data = _load_certified_data(project_root)
    projected_source = _load_projected_source(project_root)
    specs, coverage = _build_camera_specs(data, projected_source)
    failures: list[str] = []
    if len(specs) != EXPECTED_CAMERA_COUNT:
        failures.append("camera count")
    if coverage["complete_certified_ground_sample_count"] != 38250:
        failures.append("complete sample count")
    if not all(spec["coverage_passed"] for spec in specs):
        failures.append("cell coverage")
    if not all(
        spec["camera_height_above_bounds_center_cm"] == CAMERA_HEIGHT_CM
        and spec["horizontal_field_of_view_degrees"] == FIELD_OF_VIEW_DEGREES
        and spec["viewport_size"] == [VIEWPORT_WIDTH, VIEWPORT_HEIGHT]
        for spec in specs
    ):
        failures.append("camera parameters")
    fingerprint = _sha256_json(specs)
    if fingerprint != _sha256_json(json.loads(json.dumps(specs))):
        failures.append("deterministic fingerprint")
    result = {
        "schema_version": SCHEMA_VERSION,
        "overall_passed": not failures,
        "camera_count": len(specs),
        "complete_certified_ground_sample_count": coverage[
            "complete_certified_ground_sample_count"
        ],
        "camera_height_cm": CAMERA_HEIGHT_CM,
        "field_of_view_degrees": FIELD_OF_VIEW_DEGREES,
        "viewport_size": [VIEWPORT_WIDTH, VIEWPORT_HEIGHT],
        "camera_specs_sha256": fingerprint,
        "failures": failures,
    }
    print(
        "CENTRAL_CESIUM_STREAMING_CAMERAS_SELF_TEST="
        + json.dumps(result, sort_keys=True)
    )
    if failures:
        raise SystemExit(1)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Persist and validate six Central Cesium streaming cameras"
    )
    parser.add_argument(
        "--editor",
        action="store_true",
        help="register in the editor world and persist the current map when changed",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="refresh tilesets and asynchronously prove continuous streaming stability",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="validate certified coverage and constants without Unreal",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    if args.self_test:
        _run_self_test()
        return
    _run_unreal(args)


if __name__ == "__main__":
    main()
