"""Draw a transient, color-coded Central network review overlay in Unreal.

No actors are spawned and the level is never saved. Debug lines expire after
``DURATION_SECONDS``. Imported classes, manual-review candidates, explicit
corrections, certified lanes, rejected OSM ways, portals and spawn districts
all use distinct colors so topology decisions can be reviewed against Cesium.
"""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

import unreal


OVERLAY_VERSION = "1.1.0"
DURATION_SECONDS = 180.0
SOURCE_Z_OFFSET_CM = 90.0
CERTIFIED_Z_OFFSET_CM = 110.0
SNAP_SOURCE_TO_WORLD_FIRST_BLOCKER = True
FOCUS_VIEWPORT_ON_CORE = True
SOURCE_TRACE_UP_CM = 50000.0
SOURCE_TRACE_DOWN_CM = 10000.0

SOURCE_RELATIVE_PATH = Path(
    "Scripts/OpenMassCrowd/CentralNetwork/Data/central_pedestrian_source.json"
)
PROJECTED_RELATIVE_PATH = Path(
    "Scripts/OpenMassCrowd/CentralNetwork/Data/central_pedestrian_source_unreal.json"
)
RAW_OSM_RELATIVE_PATH = Path(
    "Scripts/OpenMassCrowd/CentralNetwork/Data/central_osm_overpass_raw.json"
)
CORRECTIONS_RELATIVE_PATH = Path(
    "Scripts/OpenMassCrowd/CentralNetwork/central_manual_corrections.json"
)
CERTIFIED_RELATIVE_PATH = Path(
    "Scripts/OpenMassCrowd/CentralNetwork/Data/central_network_certified.json"
)
CERTIFIED_SCHEMA_RELATIVE_PATH = Path(
    "Scripts/OpenMassCrowd/CentralNetwork/central_network_certified.schema.json"
)
WORKING_RELATIVE_PATH = Path(
    "Scripts/OpenMassCrowd/CentralNetwork/Data/central_network_certification_working.json"
)
REPORT_RELATIVE_PATH = Path(
    "Saved/Reports/central_network_review_overlay_latest.json"
)


COLORS = {
    "imported-footway": unreal.LinearColor(0.0, 0.65, 1.0, 1.0),
    "imported-sidewalk": unreal.LinearColor(0.15, 0.35, 1.0, 1.0),
    "crossing": unreal.LinearColor(1.0, 0.85, 0.0, 1.0),
    "pedestrian-zone": unreal.LinearColor(0.2, 0.85, 0.9, 1.0),
    "manual-review": unreal.LinearColor(1.0, 0.35, 0.0, 1.0),
    "manual-correction": unreal.LinearColor(0.9, 0.05, 1.0, 1.0),
    "pending-manual-connector": unreal.LinearColor(1.0, 0.0, 0.75, 1.0),
    "accepted-generated-review": unreal.LinearColor(0.95, 0.95, 1.0, 1.0),
    "certified-lane": unreal.LinearColor(0.0, 1.0, 0.15, 1.0),
    "rejected-candidate": unreal.LinearColor(1.0, 0.02, 0.02, 1.0),
    "portal": unreal.LinearColor(1.0, 0.1, 0.65, 1.0),
    "spawn-district": unreal.LinearColor(0.1, 0.45, 1.0, 1.0),
}


def project_root():
    return Path(unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_dir()))


def load_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json_atomic(path, document):
    """Replace a report even when OneDrive restored its read-only attribute."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(path.name + ".tmp")
    for candidate in (path, temporary_path):
        if candidate.exists():
            os.chmod(candidate, stat.S_IREAD | stat.S_IWRITE)
    temporary_path.write_text(
        json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2)
        + chr(10),
        encoding="utf-8",
    )
    os.replace(temporary_path, path)


def load_common_module(root):
    module_dir = root / "Scripts/OpenMassCrowd/CentralNetwork"
    if str(module_dir) not in sys.path:
        sys.path.insert(0, str(module_dir))
    import central_certified_import_common as common

    return common


def find_georeference():
    candidates = [
        actor
        for actor in unreal.EditorLevelLibrary.get_all_level_actors()
        if "CesiumGeoreference" in actor.get_class().get_name()
    ]
    if len(candidates) != 1:
        raise RuntimeError(
            "expected exactly one loaded CesiumGeoreference, found {}".format(
                [actor.get_path_name() for actor in candidates]
            )
        )
    return candidates[0]


def llh_to_unreal(geo, longitude, latitude, height=0.0):
    values = [float(longitude), float(latitude), float(height)]
    if hasattr(geo, "transform_longitude_latitude_height_position_to_unreal"):
        return geo.transform_longitude_latitude_height_position_to_unreal(values)
    return geo.transform_longitude_latitude_height_to_unreal(values)


def vector(values, z_offset=0.0):
    return unreal.Vector(
        float(values[0]), float(values[1]), float(values[2]) + float(z_offset)
    )


def hit_property(hit, name, default=None):
    try:
        return getattr(hit, name)
    except Exception:
        try:
            return hit.get_editor_property(name)
        except Exception:
            return default


def collect_cesium_components():
    components = []
    for actor in unreal.EditorLevelLibrary.get_all_level_actors():
        if actor.get_class().get_name() != "Cesium3DTileset":
            continue
        for component in actor.get_components_by_class(unreal.PrimitiveComponent):
            if (
                component.get_class().get_name() == "CesiumGltfPrimitiveComponent"
                and component.is_visible()
                and component.is_query_collision_enabled()
            ):
                components.append(component)
    return components


def snap_to_first_blocker(world, position, cesium_components):
    if not SNAP_SOURCE_TO_WORLD_FIRST_BLOCKER:
        return position, False
    start = position + unreal.Vector(0.0, 0.0, SOURCE_TRACE_UP_CM)
    end = position - unreal.Vector(0.0, 0.0, SOURCE_TRACE_DOWN_CM)
    hit = unreal.SystemLibrary.line_trace_single(
        world,
        start,
        end,
        unreal.TraceTypeQuery.TRACE_TYPE_QUERY1,
        False,
        [],
        unreal.DrawDebugTrace.NONE,
        True,
    )
    if not hit_property(hit, "blocking_hit", False):
        nearest = None
        nearest_distance = None
        # This is a one-shot editor review operation, not a runtime grounding
        # path. Direct component traces are used only when the world trace does
        # not expose streamed Cesium collision in editor mode.
        for component in cesium_components:
            component_hit = component.line_trace_component(
                start, end, True, False, False
            )
            if not component_hit:
                continue
            point = component_hit[0]
            distance = start.z - point.z
            if distance < 0.0:
                continue
            if nearest is None or distance < nearest_distance:
                nearest = point
                nearest_distance = distance
        return (nearest, True) if nearest is not None else (position, False)
    impact = hit_property(hit, "impact_point", None) or hit_property(hit, "location", None)
    return (impact, True) if impact is not None else (position, False)


def draw_polyline(world, points, color, thickness, counters, category):
    segment_count = 0
    for start, end in zip(points, points[1:]):
        unreal.SystemLibrary.draw_debug_line(
            world,
            start,
            end,
            color,
            DURATION_SECONDS,
            thickness,
        )
        segment_count += 1
    counters[category] = counters.get(category, 0) + segment_count


def source_category(feature):
    if (
        feature.get("origin", {}).get("provider") == "manual-correction"
        or feature.get("admission_rule")
        in {"allow-manual-reclassification", "allow-accepted-manual-add"}
    ):
        return "manual-correction"
    if feature["requires_manual_review"]:
        return "manual-review"
    classification = feature["classification"]
    if classification == "crossing":
        return "crossing"
    if classification == "sidewalk":
        return "imported-sidewalk"
    if classification == "pedestrian-zone":
        return "pedestrian-zone"
    if classification == "manual-pedestrian-link":
        return "manual-correction"
    return "imported-footway"


def draw_source(world, projected, counters, snap_cache, cesium_components):
    snap_success = 0
    snap_failure = 0
    for feature in projected["features"]:
        points = []
        for point in feature["points"]:
            point_id = point["point_id"]
            if point_id not in snap_cache:
                snapped, success = snap_to_first_blocker(
                    world, vector(point["unreal_position_cm"]), cesium_components
                )
                snap_cache[point_id] = snapped + unreal.Vector(
                    0.0, 0.0, SOURCE_Z_OFFSET_CM
                )
                if success:
                    snap_success += 1
                else:
                    snap_failure += 1
            points.append(snap_cache[point_id])
        category = source_category(feature)
        draw_polyline(world, points, COLORS[category], 2.5, counters, category)
    return snap_success, snap_failure


def raw_rejected_ways(raw, source):
    rejected_ids = {
        int(example["origin_id"].removeprefix("osm-way-"))
        for example in source["audit"]["rejected_examples"]
        if example["origin_id"].startswith("osm-way-")
    }
    nodes = {
        int(element["id"]): (float(element["lon"]), float(element["lat"]))
        for element in raw["elements"]
        if element.get("type") == "node"
    }
    return [
        (way, nodes)
        for way in raw["elements"]
        if way.get("type") == "way" and int(way["id"]) in rejected_ids
    ]


def draw_rejected(
    world, geo, raw, source, counters, coordinate_cache, cesium_components
):
    way_count = 0
    for way, nodes in raw_rejected_ways(raw, source):
        points = []
        for node_id in way.get("nodes", []):
            if int(node_id) not in nodes:
                continue
            longitude, latitude = nodes[int(node_id)]
            key = (round(longitude, 8), round(latitude, 8))
            if key not in coordinate_cache:
                projected = llh_to_unreal(geo, longitude, latitude)
                snapped, _success = snap_to_first_blocker(
                    world, projected, cesium_components
                )
                coordinate_cache[key] = snapped + unreal.Vector(
                    0.0, 0.0, SOURCE_Z_OFFSET_CM + 30.0
                )
            points.append(coordinate_cache[key])
        if len(points) >= 2:
            draw_polyline(
                world,
                points,
                COLORS["rejected-candidate"],
                3.5,
                counters,
                "rejected-candidate",
            )
            way_count += 1
    return way_count


def draw_manual_corrections(
    world, geo, raw, corrections, counters, coordinate_cache, cesium_components
):
    raw_nodes = {
        int(element["id"]): [float(element["lon"]), float(element["lat"])]
        for element in raw["elements"]
        if element.get("type") == "node"
    }
    raw_ways = {
        "osm-way-{}".format(int(element["id"])): element
        for element in raw["elements"]
        if element.get("type") == "way"
    }
    correction_count = 0
    for correction in corrections.get("corrections", []):
        geometry = correction.get("points_wgs84")
        if not geometry and correction.get("target_parent_feature_id") in raw_ways:
            way = raw_ways[correction["target_parent_feature_id"]]
            geometry = [
                raw_nodes[int(node_id)]
                for node_id in way.get("nodes", [])
                if int(node_id) in raw_nodes
            ]
        if not geometry:
            continue
        points = []
        for longitude, latitude in geometry:
            key = (round(float(longitude), 8), round(float(latitude), 8))
            if key not in coordinate_cache:
                projected = llh_to_unreal(geo, longitude, latitude)
                snapped, _success = snap_to_first_blocker(
                    world, projected, cesium_components
                )
                coordinate_cache[key] = snapped + unreal.Vector(
                    0.0, 0.0, SOURCE_Z_OFFSET_CM + 50.0
                )
            points.append(coordinate_cache[key])
        draw_polyline(
            world,
            points,
            COLORS["manual-correction"],
            5.0,
            counters,
            "manual-correction",
        )
        correction_count += 1
    return correction_count


def draw_certified(world, certified, counters):
    lane_count = 0
    portal_count = 0
    for cell in certified["cells"]:
        for lane in cell["directed_lanes"]:
            points = [
                vector(sample["center_position"], CERTIFIED_Z_OFFSET_CM)
                for sample in lane["ground_samples"]
            ]
            draw_polyline(
                world,
                points,
                COLORS["certified-lane"],
                6.0,
                counters,
                "certified-lane",
            )
            lane_count += 1
        for portal in cell["portals"]:
            unreal.SystemLibrary.draw_debug_sphere(
                world,
                vector(portal["position"], CERTIFIED_Z_OFFSET_CM),
                90.0,
                12,
                COLORS["portal"],
                DURATION_SECONDS,
                5.0,
            )
            portal_count += 1
    for district in certified["spawn_districts"]:
        bounds = district["world_bounds"]
        minimum = vector(bounds["min"])
        maximum = vector(bounds["max"])
        center = (minimum + maximum) * 0.5 + unreal.Vector(0.0, 0.0, 160.0)
        extent = (maximum - minimum) * 0.5
        extent.z = max(80.0, extent.z)
        unreal.SystemLibrary.draw_debug_box(
            world,
            center,
            extent,
            COLORS["spawn-district"],
            unreal.Rotator(0.0, 0.0, 0.0),
            DURATION_SECONDS,
            3.0,
        )
        unreal.SystemLibrary.draw_debug_string(
            world,
            center + unreal.Vector(0.0, 0.0, 180.0),
            "{} population={}".format(
                district["district_id"], district["target_population"]
            ),
            None,
            COLORS["spawn-district"],
            DURATION_SECONDS,
        )
    return lane_count, portal_count


def draw_working_review_candidates(world, working, counters):
    """Draw only collision-accepted connectors that still require review."""

    candidates = {}
    for key in ("generated_connector_candidates", "semantic_recovery_candidates"):
        for candidate in working.get(key, []):
            candidates[candidate["candidate_id"]] = candidate

    category_counts = {
        "pending-manual-connector": 0,
        "accepted-generated-review": 0,
    }
    for candidate_id, result in working.get("results", {}).items():
        if result.get("status") != "accepted":
            continue
        candidate = candidates.get(candidate_id)
        if candidate is None:
            continue
        origin = candidate.get("topology_origin")
        if origin == "manual-cesium-connector":
            category = "pending-manual-connector"
            thickness = 10.0
        elif origin == "generated-connector":
            category = "accepted-generated-review"
            thickness = 7.0
        else:
            continue
        points = [
            vector(sample["center_position"], CERTIFIED_Z_OFFSET_CM + 40.0)
            for sample in result.get("samples", [])
        ]
        if len(points) < 2:
            continue
        draw_polyline(
            world,
            points,
            COLORS[category],
            thickness,
            counters,
            category,
        )
        category_counts[category] += 1
    return category_counts


def color_as_list(color):
    return [
        round(float(color.r), 3),
        round(float(color.g), 3),
        round(float(color.b), 3),
        round(float(color.a), 3),
    ]


def focus_viewport(projected):
    if not FOCUS_VIEWPORT_ON_CORE:
        return
    bounds = projected["world_bounds_cm"]
    minimum = vector(bounds["min"])
    maximum = vector(bounds["max"])
    center = (minimum + maximum) * 0.5
    camera = center + unreal.Vector(0.0, -26000.0, 32000.0)
    rotation = unreal.MathLibrary.find_look_at_rotation(camera, center)
    unreal.get_editor_subsystem(
        unreal.UnrealEditorSubsystem
    ).set_level_viewport_camera_info(camera, rotation)


def main():
    root = project_root()
    world = unreal.EditorLevelLibrary.get_editor_world()
    if world is None:
        raise RuntimeError("no editor world is loaded")
    geo = find_georeference()
    source = load_json(root / SOURCE_RELATIVE_PATH)
    projected = load_json(root / PROJECTED_RELATIVE_PATH)
    raw = load_json(root / RAW_OSM_RELATIVE_PATH)
    corrections = load_json(root / CORRECTIONS_RELATIVE_PATH)
    focus_viewport(projected)
    counters = {}
    snap_cache = {}
    coordinate_cache = {}
    cesium_components = collect_cesium_components()
    snap_success, snap_failure = draw_source(
        world, projected, counters, snap_cache, cesium_components
    )
    rejected_way_count = draw_rejected(
        world, geo, raw, source, counters, coordinate_cache, cesium_components
    )
    correction_count = draw_manual_corrections(
        world,
        geo,
        raw,
        corrections,
        counters,
        coordinate_cache,
        cesium_components,
    )

    certified_path = root / CERTIFIED_RELATIVE_PATH
    certified_lane_count = 0
    portal_count = 0
    certified_loaded = False
    if certified_path.exists():
        common = load_common_module(root)
        certified = common.load_json_strict(certified_path)
        schema = common.load_json_strict(root / CERTIFIED_SCHEMA_RELATIVE_PATH)
        common.validate_certified_document(certified, schema)
        certified_lane_count, portal_count = draw_certified(world, certified, counters)
        certified_loaded = True

    working_path = root / WORKING_RELATIVE_PATH
    working_review_counts = {
        "pending-manual-connector": 0,
        "accepted-generated-review": 0,
    }
    if working_path.exists():
        working_review_counts = draw_working_review_candidates(
            world, load_json(working_path), counters
        )

    report = {
        "schema_version": 1,
        "overlay_version": OVERLAY_VERSION,
        "world": world.get_name(),
        "transient": True,
        "duration_seconds": DURATION_SECONDS,
        "level_saved": False,
        "surface_snap_enabled": SNAP_SOURCE_TO_WORLD_FIRST_BLOCKER,
        "viewport_focused_on_core": FOCUS_VIEWPORT_ON_CORE,
        "cesium_component_count": len(cesium_components),
        "surface_snap_success_count": snap_success,
        "surface_snap_failure_count": snap_failure,
        "source_feature_count": len(projected["features"]),
        "manual_correction_count": correction_count,
        "rejected_way_count": rejected_way_count,
        "certified_cache_loaded": certified_loaded,
        "certified_lane_count": certified_lane_count,
        "portal_count": portal_count,
        "working_review_candidate_counts": dict(sorted(working_review_counts.items())),
        "drawn_segment_counts": dict(sorted(counters.items())),
        "legend": {
            name: color_as_list(color) for name, color in sorted(COLORS.items())
        },
    }
    report_path = root / REPORT_RELATIVE_PATH
    write_json_atomic(report_path, report)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))


try:
    main()
except Exception as error:
    unreal.log_error("CENTRAL_NETWORK_REVIEW_OVERLAY_ERROR {}".format(error))
    raise
