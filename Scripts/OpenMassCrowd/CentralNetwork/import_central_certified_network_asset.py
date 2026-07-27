"""Strictly import the Ground-Only certified Central JSON into its UE DataAsset.

Run this file inside the open TelecomTwin editor through
``run_unreal_python_via_mcp.py``.  The input path is intentionally fixed: a
projected/source candidate cannot be substituted on the command line.  The
entire document is schema-, hash-, certification- and provenance-validated
before any asset object is created or mutated.
"""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

import unreal


IMPORTER_VERSION = "1.2.0"
CERTIFIED_RELATIVE_PATH = Path(
    "Scripts/OpenMassCrowd/CentralNetwork/Data/central_network_ground_only.json"
)
SCHEMA_RELATIVE_PATH = Path(
    "Scripts/OpenMassCrowd/CentralNetwork/central_network_certified.schema.json"
)
SOURCE_RELATIVE_PATH = Path(
    "Scripts/OpenMassCrowd/CentralNetwork/Data/central_pedestrian_source.json"
)
PARENT_RELATIVE_PATH = Path(
    "Scripts/OpenMassCrowd/CentralNetwork/Data/central_network_certified.json"
)
POLICY_RELATIVE_PATH = Path(
    "Scripts/OpenMassCrowd/CentralNetwork/central_ground_only_policy.json"
)
AUDIT_RELATIVE_PATH = Path(
    "Scripts/OpenMassCrowd/CentralNetwork/Data/central_network_ground_only_asset_import.audit.json"
)
ASSET_DIRECTORY = "/Game/OpenMassCrowd/Central"
ASSET_NAME = "DA_CentralNetwork_Certified"
ASSET_PATH = ASSET_DIRECTORY + "/" + ASSET_NAME


def project_root():
    return Path(unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_dir()))


def load_validation_modules(root):
    module_dir = root / "Scripts/OpenMassCrowd/CentralNetwork"
    if str(module_dir) not in sys.path:
        sys.path.insert(0, str(module_dir))
    import central_certified_import_common as common
    import verify_central_ground_only_network as ground_only_verify

    return common, ground_only_verify


def require_unreal_type(name):
    value = getattr(unreal, name, None)
    if value is None:
        raise RuntimeError(
            "OpenMassCrowd reflected type {} is unavailable. Rebuild the plugin "
            "and restart Unreal Editor before importing.".format(name)
        )
    return value


def set_property(value, name, property_value):
    value.set_editor_property(name, property_value)
    return value


def name_value(value):
    return unreal.Name(str(value))


def name_values(values):
    return [name_value(value) for value in sorted(values)]


def vector(values):
    return unreal.Vector(float(values[0]), float(values[1]), float(values[2]))


def box(bounds):
    result = unreal.Box()
    result.set_editor_property("min", vector(bounds["min"]))
    result.set_editor_property("max", vector(bounds["max"]))
    result.set_editor_property("is_valid", True)
    return result


def int_point(values):
    result = unreal.IntPoint()
    result.set_editor_property("x", int(values[0]))
    result.set_editor_property("y", int(values[1]))
    return result


def signed_int32(value):
    return value - 0x100000000 if value >= 0x80000000 else value


def guid(value):
    parsed = uuid.UUID(value)
    hexadecimal = parsed.hex
    parts = [int(hexadecimal[index : index + 8], 16) for index in range(0, 32, 8)]
    result = unreal.Guid()
    for property_name, part in zip(("a", "b", "c", "d"), parts):
        result.set_editor_property(property_name, signed_int32(part))
    return result


def normalised_enum_name(value):
    return "".join(character for character in str(value).lower() if character.isalnum())


def enum_value(type_name, json_value):
    enum_type = require_unreal_type(type_name)
    expected = normalised_enum_name(json_value)
    aliases = {
        "openstreetmap": {"openstreetmap"},
        "manualcorrection": {"manualcorrection"},
        "generatedconnector": {"generatedconnector"},
        "osmsemanticrecovery": {"osmsemanticrecovery"},
        "pedestrianzone": {"pedestrianzone"},
        "manualpedestrianlink": {"manualpedestrianlink"},
        "crossingendpoint": {"crossingendpoint"},
        "spawnanchor": {"spawnanchor"},
    }
    accepted = aliases.get(expected, {expected})
    for attribute_name in dir(enum_type):
        if attribute_name.startswith("_"):
            continue
        if normalised_enum_name(attribute_name) in accepted:
            return getattr(enum_type, attribute_name)
    raise RuntimeError(
        "cannot map JSON enum {!r} to reflected {} values {}".format(
            json_value,
            type_name,
            [name for name in dir(enum_type) if name.isupper()],
        )
    )


HASH_PROPERTIES = {
    "algorithm": "hash_algorithm",
    "topology_sha256": "topology_sha256",
    "georeference_sha256": "georeference_sha256",
    "tileset_sha256": "tileset_sha256",
    "collision_settings_sha256": "collision_settings_sha256",
    "cell_content_sha256": "cell_content_sha256",
    "combined_sha256": "combined_sha256",
}

EVIDENCE_PROPERTIES = {
    "candidate_lane_count": "candidate_lane_count",
    "certified_lane_count": "certified_lane_count",
    "rejected_lane_count": "rejected_lane_count",
    "coarse_support_check_count": "coarse_support_check_count",
    "strict_ground_sample_count": "strict_ground_sample_count",
    "exact_xy_support_pass_count": "exact_xy_support_pass_count",
    "first_blocker_pass_count": "first_blocker_pass_count",
    "height_continuity_pass_count": "height_continuity_pass_count",
    "slope_pass_count": "slope_pass_count",
    "multi_track_support_pass_count": "multi_track_support_pass_count",
    "capsule_clearance_pass_count": "capsule_clearance_pass_count",
    "missing_support_rejection_count": "missing_support_rejection_count",
    "first_blocker_rejection_count": "first_blocker_rejection_count",
    "height_continuity_rejection_count": "height_continuity_rejection_count",
    "slope_rejection_count": "slope_rejection_count",
    "multi_track_rejection_count": "multi_track_rejection_count",
    "capsule_clearance_rejection_count": "capsule_clearance_rejection_count",
    "connected_component_count": "connected_component_count",
    "street_block_count": "street_block_count",
    "certified_geographic_block_count": "certified_geographic_block_count",
    "junction_count": "junction_count",
    "portal_count": "portal_count",
    "spawn_district_count": "spawn_district_count",
    "certified_directional_lane_length_cm": "certified_directional_lane_length_cm",
    "whole_area_recertification_count": "whole_area_recertification_count",
}


def make_hashes(data):
    result = require_unreal_type("OpenMassCrowdCentralNetworkHashes")()
    for json_name, property_name in HASH_PROPERTIES.items():
        result.set_editor_property(property_name, str(data[json_name]))
    return result


def make_evidence(data):
    result = require_unreal_type("OpenMassCrowdCentralCertificationEvidence")()
    for json_name, property_name in EVIDENCE_PROPERTIES.items():
        result.set_editor_property(property_name, data[json_name])
    return result


def make_ground_sample(data):
    result = require_unreal_type("OpenMassCrowdCentralGroundSample")()
    result.set_editor_property("sample_id", name_value(data["sample_id"]))
    result.set_editor_property("sample_index", int(data["sample_index"]))
    result.set_editor_property("distance_along_lane_cm", float(data["distance_along_lane_cm"]))
    result.set_editor_property("center_position", vector(data["center_position"]))
    result.set_editor_property("left_track_position", vector(data["left_track_position"]))
    result.set_editor_property("right_track_position", vector(data["right_track_position"]))
    result.set_editor_property("surface_normal", vector(data["surface_normal"]))
    result.set_editor_property("surface_slope_degrees", float(data["surface_slope_degrees"]))
    result.set_editor_property(
        "max_neighbor_height_delta_cm", float(data["max_neighbor_height_delta_cm"])
    )
    result.set_editor_property(
        "supporting_primitive_id", name_value(data["supporting_primitive_id"])
    )
    result.set_editor_property("evidence_mask", int(data["evidence_mask"]))
    return result


def make_node(data):
    result = require_unreal_type("OpenMassCrowdCentralNode")()
    result.set_editor_property("node_id", name_value(data["node_id"]))
    result.set_editor_property("cell_id", name_value(data["cell_id"]))
    result.set_editor_property("component_id", name_value(data["component_id"]))
    result.set_editor_property(
        "kind", enum_value("OpenMassCrowdCentralNodeKind", data["kind"])
    )
    result.set_editor_property("position", vector(data["position"]))
    result.set_editor_property("incoming_lane_ids", name_values(data["incoming_lane_ids"]))
    result.set_editor_property("outgoing_lane_ids", name_values(data["outgoing_lane_ids"]))
    return result


def make_lane(data):
    result = require_unreal_type("OpenMassCrowdCentralDirectedLane")()
    for property_name, json_name in (
        ("lane_id", "lane_id"),
        ("cell_id", "cell_id"),
        ("component_id", "component_id"),
        ("source_feature_id", "source_feature_id"),
        ("from_node_id", "from_node_id"),
        ("to_node_id", "to_node_id"),
        ("reverse_lane_id", "reverse_lane_id"),
    ):
        result.set_editor_property(property_name, name_value(data[json_name]))
    result.set_editor_property(
        "topology_origin",
        enum_value("OpenMassCrowdCentralTopologyOrigin", data["topology_origin"]),
    )
    result.set_editor_property(
        "pedestrian_class",
        enum_value("OpenMassCrowdCentralPedestrianClass", data["pedestrian_class"]),
    )
    result.set_editor_property("width_cm", float(data["width_cm"]))
    result.set_editor_property("length_cm", float(data["length_cm"]))
    result.set_editor_property("certified", True)
    result.set_editor_property("ground_only_eligible", data["ground_only_eligible"] is True)
    samples = sorted(data["ground_samples"], key=lambda sample: sample["sample_index"])
    result.set_editor_property("ground_samples", [make_ground_sample(sample) for sample in samples])
    return result


def make_portal(data):
    result = require_unreal_type("OpenMassCrowdCentralPortal")()
    for property_name in (
        "portal_id",
        "reverse_portal_id",
        "local_cell_id",
        "remote_cell_id",
        "local_node_id",
        "remote_node_id",
        "directed_lane_id",
    ):
        result.set_editor_property(property_name, name_value(data[property_name]))
    result.set_editor_property("position", vector(data["position"]))
    result.set_editor_property("certified", True)
    return result


def make_cell(data):
    result = require_unreal_type("OpenMassCrowdCentralCell")()
    result.set_editor_property("cell_id", name_value(data["cell_id"]))
    result.set_editor_property("grid_coordinate", int_point(data["grid_coordinate"]))
    result.set_editor_property("world_bounds", box(data["world_bounds"]))
    result.set_editor_property("source_feature_ids", name_values(data["source_feature_ids"]))
    result.set_editor_property(
        "nodes", [make_node(node) for node in sorted(data["nodes"], key=lambda node: node["node_id"])]
    )
    result.set_editor_property(
        "directed_lanes",
        [
            make_lane(lane)
            for lane in sorted(data["directed_lanes"], key=lambda lane: lane["lane_id"])
        ],
    )
    result.set_editor_property(
        "portals",
        [
            make_portal(portal)
            for portal in sorted(data["portals"], key=lambda portal: portal["portal_id"])
        ],
    )
    result.set_editor_property("hashes", make_hashes(data["hashes"]))
    result.set_editor_property("evidence", make_evidence(data["evidence"]))
    result.set_editor_property("certified", True)
    return result


def make_district(data):
    result = require_unreal_type("OpenMassCrowdCentralSpawnDistrict")()
    result.set_editor_property("district_id", name_value(data["district_id"]))
    result.set_editor_property("component_id", name_value(data["component_id"]))
    result.set_editor_property("cell_ids", name_values(data["cell_ids"]))
    result.set_editor_property("spawn_node_ids", name_values(data["spawn_node_ids"]))
    result.set_editor_property("spawn_lane_ids", name_values(data["spawn_lane_ids"]))
    result.set_editor_property("world_bounds", box(data["world_bounds"]))
    result.set_editor_property("target_population", int(data["target_population"]))
    result.set_editor_property("selection_weight", float(data["selection_weight"]))
    result.set_editor_property("enabled", True)
    return result


def make_component(data):
    result = require_unreal_type("OpenMassCrowdCentralComponent")()
    result.set_editor_property("component_id", name_value(data["component_id"]))
    result.set_editor_property("cell_ids", name_values(data["cell_ids"]))
    result.set_editor_property("node_ids", name_values(data["node_ids"]))
    result.set_editor_property(
        "directed_lane_ids", name_values(data["directed_lane_ids"])
    )
    result.set_editor_property(
        "directional_lane_length_cm", float(data["directional_lane_length_cm"])
    )
    result.set_editor_property("junction_count", int(data["junction_count"]))
    result.set_editor_property("street_block_count", int(data["street_block_count"]))
    result.set_editor_property("certified", True)
    return result


def validate_source_provenance(document, source):
    provenance = document["source_provenance"]
    if provenance["dataset_id"] != source.get("dataset_id"):
        raise RuntimeError("certified source_provenance dataset_id mismatch")
    source_hashes = source.get("hashes", {})
    if provenance["topology_sha256"] != source_hashes.get("topology_sha256"):
        raise RuntimeError("certified topology is not tied to the versioned source")
    if provenance["document_sha256"] != source_hashes.get("document_sha256"):
        raise RuntimeError("certified source document hash is stale")


def build_asset_payload(document):
    # Construct every reflected value before touching an existing asset.
    ground_only = document["ground_only_filter"]
    return {
        "schema_version": 3,
        "network_id": name_value(document["network_id"]),
        "build_id": guid(document["build_id"]),
        "generator_version": str(document["generator_version"]),
        "ground_only_network": True,
        "parent_certified_sha256": ground_only["parent_file_sha256"],
        "ground_only_policy_sha256": ground_only["policy_sha256"],
        "ground_only_excluded_source_feature_count": len(
            ground_only["excluded_source_features"]
        ),
        "world_bounds": box(document["world_bounds"]),
        "hashes": make_hashes(document["hashes"]),
        "cells": [
            make_cell(cell)
            for cell in sorted(document["cells"], key=lambda cell: cell["cell_id"])
        ],
        "components": [
            make_component(component)
            for component in sorted(
                document["components"], key=lambda component: component["component_id"]
            )
        ],
        "spawn_districts": [
            make_district(district)
            for district in sorted(
                document["spawn_districts"], key=lambda district: district["district_id"]
            )
        ],
        "evidence": make_evidence(document["evidence"]),
    }


def load_or_create_asset(asset_class):
    asset = unreal.EditorAssetLibrary.load_asset(ASSET_PATH)
    if asset is not None:
        if not isinstance(asset, asset_class):
            raise RuntimeError(
                "existing {} has unexpected class {}".format(
                    ASSET_PATH, asset.get_class().get_name()
                )
            )
        return asset
    if not unreal.EditorAssetLibrary.does_directory_exist(ASSET_DIRECTORY):
        unreal.EditorAssetLibrary.make_directory(ASSET_DIRECTORY)
    factory = unreal.DataAssetFactory()
    factory.set_editor_property("data_asset_class", asset_class)
    asset = unreal.AssetToolsHelpers.get_asset_tools().create_asset(
        ASSET_NAME, ASSET_DIRECTORY, asset_class, factory
    )
    if asset is None:
        raise RuntimeError("failed to create {}".format(ASSET_PATH))
    return asset


def write_audit(path, common, document, schema_path, certified_path, metrics):
    audit = {
        "schema_version": 1,
        "importer_version": IMPORTER_VERSION,
        "validator_version": common.IMPORT_VALIDATOR_VERSION,
        "asset_path": ASSET_PATH,
        "asset_class": "OpenMassCrowdCentralNetworkDataAsset",
        "input_path": str(CERTIFIED_RELATIVE_PATH).replace("\\", "/"),
        "input_file_sha256": common.file_sha256(certified_path),
        "input_document_sha256": common.sha256_json(document),
        "schema_path": str(SCHEMA_RELATIVE_PATH).replace("\\", "/"),
        "schema_file_sha256": common.file_sha256(schema_path),
        "network_id": document["network_id"],
        "build_id": document["build_id"],
        "hashes": document["hashes"],
        "strict_gates": {
            "fixed_certified_input_path": True,
            "duplicate_keys_rejected": True,
            "schema_valid": metrics["schema_valid"],
            "canonical_hashes_valid": metrics["hashes_valid"],
            "certified_only": metrics["certified_only"],
            "source_provenance_valid": metrics["source_provenance_valid"],
            "ground_only_valid": metrics["ground_only_valid"],
            "zero_elevated_source_lanes": metrics["zero_elevated_source_lanes"],
            "candidate_source_cannot_create_asset": True,
        },
        "ground_only": {
            "policy_id": metrics["policy_id"],
            "policy_sha256": metrics["policy_sha256"],
            "parent_file_sha256": metrics["parent_file_sha256"],
            "active_source_feature_count": metrics["active_source_feature_count"],
            "excluded_source_feature_count": metrics["excluded_source_feature_count"],
            "excluded_directional_lane_count": metrics[
                "excluded_directional_lane_count"
            ],
        },
        "counts": {
            key: metrics[key]
            for key in (
                "cell_count",
                "node_count",
                "lane_count",
                "ground_sample_count",
                "portal_count",
                "component_count",
                "spawn_district_count",
                "target_population",
            )
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(audit, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        + chr(10),
        encoding="utf-8",
    )
    return audit


def main():
    root = project_root()
    common, ground_only_verify = load_validation_modules(root)
    certified_path = root / CERTIFIED_RELATIVE_PATH
    schema_path = root / SCHEMA_RELATIVE_PATH
    source_path = root / SOURCE_RELATIVE_PATH
    parent_path = root / PARENT_RELATIVE_PATH
    policy_path = root / POLICY_RELATIVE_PATH
    audit_path = root / AUDIT_RELATIVE_PATH
    if certified_path.name != "central_network_ground_only.json":
        raise RuntimeError("certified importer input path invariant was modified")
    document = common.load_json_strict(certified_path)
    schema = common.load_json_strict(schema_path)
    source = common.load_json_strict(source_path)
    parent = common.load_json_strict(parent_path)
    policy = common.load_json_strict(policy_path)
    metrics = ground_only_verify.validate_ground_only_document(
        document,
        parent,
        source,
        policy,
        parent_path,
        policy_path,
        schema,
    )
    validate_source_provenance(document, source)

    asset_class = require_unreal_type("OpenMassCrowdCentralNetworkDataAsset")
    payload = build_asset_payload(document)
    asset = load_or_create_asset(asset_class)
    for property_name, property_value in payload.items():
        asset.set_editor_property(property_name, property_value)
    if not unreal.EditorAssetLibrary.save_asset(ASSET_PATH):
        raise RuntimeError("failed to save {}".format(ASSET_PATH))
    audit = write_audit(
        audit_path, common, document, schema_path, certified_path, metrics
    )
    print(
        json.dumps(
            {
                "status": "ok",
                "asset_path": ASSET_PATH,
                "audit_path": str(audit_path),
                "network_id": audit["network_id"],
                "build_id": audit["build_id"],
                "counts": audit["counts"],
                "strict_gates": audit["strict_gates"],
            },
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
    )


try:
    main()
except Exception as error:
    unreal.log_error("CENTRAL_CERTIFIED_ASSET_IMPORT_ERROR {}".format(error))
    raise
