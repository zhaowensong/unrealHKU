"""Project the validated Central WGS84 source through the live Cesium georeference.

Run inside the open TelecomTwin Unreal Editor via run_unreal_python_via_mcp.py.
This creates a deterministic JSON mirror for the UE network data asset.  It
does not trace the tileset and every candidate remains fail-closed until the
strict Cesium ground-certification stage fills nodes, lanes, portals and Z.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import unreal


SCHEMA_VERSION = 1
GENERATOR_VERSION = "1.0.0"
PROJECTION_HEIGHT_M = 0.0
SOURCE_RELATIVE_PATH = Path(
    "Scripts/OpenMassCrowd/CentralNetwork/Data/central_pedestrian_source.json"
)
OUTPUT_RELATIVE_PATH = Path(
    "Scripts/OpenMassCrowd/CentralNetwork/Data/central_pedestrian_source_unreal.json"
)
AUDIT_RELATIVE_PATH = Path(
    "Scripts/OpenMassCrowd/CentralNetwork/Data/central_pedestrian_source_unreal.audit.json"
)
EXPECTED_WORLD = "shanghai"


def canonical_json_bytes(value):
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + chr(10)
    ).encode("utf-8")


def sha256_value(value):
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        + chr(10),
        encoding="utf-8",
    )


def round_vector(vector):
    return [round(float(vector.x), 4), round(float(vector.y), 4), round(float(vector.z), 4)]


def vector_bounds(vectors):
    if not vectors:
        raise RuntimeError("cannot calculate an empty world bound")
    return {
        "min": [round(min(vector[index] for vector in vectors), 4) for index in range(3)],
        "max": [round(max(vector[index] for vector in vectors), 4) for index in range(3)],
    }


def find_georeference():
    actors = unreal.EditorLevelLibrary.get_all_level_actors()
    candidates = [
        actor
        for actor in actors
        if "CesiumGeoreference" in actor.get_class().get_name()
    ]
    if len(candidates) != 1:
        raise RuntimeError(
            "expected exactly one loaded CesiumGeoreference actor, found {}".format(
                [actor.get_path_name() for actor in candidates]
            )
        )
    return candidates[0]


def safe_property(actor, name):
    try:
        value = actor.get_editor_property(name)
    except Exception:
        return None
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if hasattr(value, "name"):
        try:
            return str(value.name)
        except Exception:
            pass
    return str(value)


def georeference_snapshot(geo, world_name):
    property_names = (
        "origin_placement",
        "origin_longitude",
        "origin_latitude",
        "origin_height",
        "scale",
        "keep_world_origin_near_camera",
        "maximum_world_origin_distance",
    )
    properties = {
        name: safe_property(geo, name)
        for name in property_names
        if safe_property(geo, name) is not None
    }
    snapshot = {
        "actor_path": geo.get_path_name(),
        "actor_class": geo.get_class().get_name(),
        "world_name": world_name,
        "properties": properties,
    }
    snapshot["sha256"] = sha256_value(snapshot)
    return snapshot


def project_position(geo, longitude, latitude, height):
    values = [float(longitude), float(latitude), float(height)]
    if hasattr(geo, "transform_longitude_latitude_height_position_to_unreal"):
        return geo.transform_longitude_latitude_height_position_to_unreal(values)
    if hasattr(geo, "transform_longitude_latitude_height_to_unreal"):
        return geo.transform_longitude_latitude_height_to_unreal(values)
    raise RuntimeError("CesiumGeoreference does not expose an LLH-to-Unreal transform")


def validate_source_minimum(source):
    if source.get("schema_version") != 1:
        raise RuntimeError("unsupported Central source schema")
    if len(source.get("cells", [])) != 6:
        raise RuntimeError("Central source must contain exactly six cells")
    if not source.get("features"):
        raise RuntimeError("Central source contains no candidate features")
    expected = source.get("hashes", {}).get("topology_sha256")
    if not isinstance(expected, str) or len(expected) != 64:
        raise RuntimeError("Central source topology hash is missing")


def projected_point(source_point, geo):
    world = project_position(
        geo,
        source_point["longitude"],
        source_point["latitude"],
        PROJECTION_HEIGHT_M,
    )
    return {
        "point_id": source_point["point_id"],
        "wgs84": [
            round(float(source_point["longitude"]), 8),
            round(float(source_point["latitude"]), 8),
            PROJECTION_HEIGHT_M,
        ],
        "unreal_position_cm": round_vector(world),
        "osm_node_id": source_point["osm_node_id"],
        "is_cell_boundary": bool(source_point["is_cell_boundary"]),
        "requires_ground_certification": True,
    }


def projected_cell_bounds(cell, geo):
    bounds = cell["bounds_wgs84"]
    corners = []
    for longitude in (bounds["west"], bounds["east"]):
        for latitude in (bounds["south"], bounds["north"]):
            corners.append(
                round_vector(
                    project_position(geo, longitude, latitude, PROJECTION_HEIGHT_M)
                )
            )
    return vector_bounds(corners)


def build_projected_document(source, geo, world_name):
    georeference = georeference_snapshot(geo, world_name)
    features = []
    all_positions = []
    for source_feature in source["features"]:
        points = [projected_point(point, geo) for point in source_feature["points"]]
        all_positions.extend(point["unreal_position_cm"] for point in points)
        feature = {
            "feature_id": source_feature["feature_id"],
            "parent_feature_id": source_feature["parent_feature_id"],
            "origin": source_feature["origin"],
            "classification": source_feature["classification"],
            "admission_rule": source_feature["admission_rule"],
            "requires_manual_review": source_feature["requires_manual_review"],
            "cell_id": source_feature["cell_id"],
            "part_index": source_feature["part_index"],
            "directionality": source_feature["directionality"],
            "points": points,
            "tags": source_feature["tags"],
            "source_geometry_sha256": source_feature["geometry_sha256"],
            "projected_geometry_sha256": sha256_value(points),
        }
        features.append(feature)
    features.sort(key=lambda feature: feature["feature_id"])

    cells = []
    for source_cell in source["cells"]:
        cell_bounds = projected_cell_bounds(source_cell, geo)
        cell_content = {
            "cell_id": source_cell["cell_id"],
            "source_feature_ids": source_cell["source_feature_ids"],
            "world_bounds_cm": cell_bounds,
        }
        cells.append(
            {
                "schema_version": SCHEMA_VERSION,
                "cell_id": source_cell["cell_id"],
                "grid_coordinate": source_cell["grid_coordinate"],
                "bounds_wgs84": source_cell["bounds_wgs84"],
                "world_bounds_cm": cell_bounds,
                "source_feature_ids": source_cell["source_feature_ids"],
                "nodes": [],
                "directed_lanes": [],
                "portals": [],
                "certified": False,
                "hashes": {
                    "topology_sha256": source["hashes"]["topology_sha256"],
                    "georeference_sha256": georeference["sha256"],
                    "cell_content_sha256": sha256_value(cell_content),
                },
            }
        )

    spawn_districts = [
        {
            "district_id": "district-" + cell["cell_id"],
            "cell_ids": [cell["cell_id"]],
            "spawn_node_ids": [],
            "spawn_lane_ids": [],
            "world_bounds_cm": cell["world_bounds_cm"],
            "target_population": 50,
            "selection_weight": 1.0,
            # There are no certified spawn lanes yet. Fail closed until the
            # certification builder explicitly enables the district.
            "enabled": False,
        }
        for cell in cells
    ]

    projected_geometry_sha256 = sha256_value(
        [
            {
                "feature_id": feature["feature_id"],
                "projected_geometry_sha256": feature["projected_geometry_sha256"],
            }
            for feature in features
        ]
    )
    combined_sha256 = sha256_value(
        {
            "topology_sha256": source["hashes"]["topology_sha256"],
            "georeference_sha256": georeference["sha256"],
            "projected_geometry_sha256": projected_geometry_sha256,
        }
    )
    return {
        "$schema": "../central_pedestrian_unreal_source.schema.json",
        "schema_version": SCHEMA_VERSION,
        "source_schema_version": source["schema_version"],
        "network_id": source["dataset_id"],
        "build_id": source["build_id"],
        "generator_version": GENERATOR_VERSION,
        "source_topology_sha256": source["hashes"]["topology_sha256"],
        "coordinate_system": {
            "source": "WGS84 longitude/latitude/ellipsoid-height (EPSG:4979)",
            "target": "TelecomTwin Unreal world",
            "unreal_units": "centimeters",
            "projection_height_m": PROJECTION_HEIGHT_M,
            "method": "CesiumGeoreference.transform_longitude_latitude_height_position_to_unreal",
            "z_policy": "reference-only; replace with strict Cesium ground certification",
        },
        "georeference": georeference,
        "world_bounds_cm": vector_bounds(all_positions),
        "cells": cells,
        "features": features,
        "spawn_districts": spawn_districts,
        "evidence": {
            "candidate_feature_count": len(features),
            "candidate_point_count": sum(len(feature["points"]) for feature in features),
            "certified_lane_count": 0,
            "rejected_lane_count": 0,
            "whole_area_recertification_count": 0,
        },
        "hashes": {
            "algorithm": "SHA-256",
            "topology_sha256": source["hashes"]["topology_sha256"],
            "georeference_sha256": georeference["sha256"],
            "projected_geometry_sha256": projected_geometry_sha256,
            "combined_sha256": combined_sha256,
        },
    }


def make_audit(projected):
    return {
        "schema_version": projected["schema_version"],
        "network_id": projected["network_id"],
        "build_id": projected["build_id"],
        "coordinate_system": projected["coordinate_system"],
        "georeference": projected["georeference"],
        "world_bounds_cm": projected["world_bounds_cm"],
        "hashes": projected["hashes"],
        "evidence": projected["evidence"],
        "cells": [
            {
                "cell_id": cell["cell_id"],
                "grid_coordinate": cell["grid_coordinate"],
                "world_bounds_cm": cell["world_bounds_cm"],
                "source_feature_count": len(cell["source_feature_ids"]),
                "certified": cell["certified"],
            }
            for cell in projected["cells"]
        ],
        "spawn_districts": projected["spawn_districts"],
    }


def main():
    project_dir = Path(
        unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_dir())
    )
    source_path = project_dir / SOURCE_RELATIVE_PATH
    output_path = project_dir / OUTPUT_RELATIVE_PATH
    audit_path = project_dir / AUDIT_RELATIVE_PATH
    source = json.loads(source_path.read_text(encoding="utf-8"))
    validate_source_minimum(source)
    world = unreal.EditorLevelLibrary.get_editor_world()
    if world is None:
        raise RuntimeError("no editor world is loaded")
    if world.get_name() != EXPECTED_WORLD:
        raise RuntimeError(
            "expected map {}, got {}; open /Game/Maps/shanghai first".format(
                EXPECTED_WORLD, world.get_name()
            )
        )
    geo = find_georeference()
    projected = build_projected_document(source, geo, world.get_name())
    write_json(output_path, projected)
    write_json(audit_path, make_audit(projected))
    print(
        json.dumps(
            {
                "status": "ok",
                "network_id": projected["network_id"],
                "build_id": projected["build_id"],
                "feature_count": len(projected["features"]),
                "point_count": projected["evidence"]["candidate_point_count"],
                "georeference_sha256": projected["hashes"]["georeference_sha256"],
                "projected_geometry_sha256": projected["hashes"]["projected_geometry_sha256"],
                "output": str(output_path),
                "audit_output": str(audit_path),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


try:
    main()
except Exception as error:
    unreal.log_error("CENTRAL_CESIUM_PROJECTION_ERROR {}".format(error))
    raise
