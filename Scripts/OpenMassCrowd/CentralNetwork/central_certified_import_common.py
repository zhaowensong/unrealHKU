"""Strict, Unreal-compatible validation for certified Central network JSON.

Unreal's embedded Python does not ship ``jsonschema``.  This module implements
the deliberately small Draft 2020-12 keyword subset used by
``central_network_certified.schema.json`` and then applies the exact canonical
hash contract used by ``verify_central_network_cache.py``.  It has no Unreal
imports so the same gates are host-testable before an asset is touched.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any, Iterable


IMPORT_VALIDATOR_VERSION = "1.1.0"
HASH_INPUT_KEYS = (
    "topology_sha256",
    "georeference_sha256",
    "tileset_sha256",
    "collision_settings_sha256",
)
FULL_GROUND_EVIDENCE_MASK = 63
EXPECTED_SCHEMA_VERSION = 2
EXPECTED_CELL_COUNT = 6
EXPECTED_DISTRICT_COUNT = 6
EXPECTED_POPULATION = 300
TRUSTED_TOPOLOGY_ORIGINS = {"openstreetmap", "osm-semantic-recovery"}


class CertifiedImportError(RuntimeError):
    """Raised before any Unreal asset mutation when an import gate fails."""


class DuplicateKeyError(ValueError):
    pass


def reject_duplicate_keys(pairs: Iterable[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateKeyError(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def load_json_strict(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8-sig") as stream:
            return json.load(stream, object_pairs_hook=reject_duplicate_keys)
    except (OSError, UnicodeError, json.JSONDecodeError, DuplicateKeyError) as error:
        raise CertifiedImportError(f"failed to load strict JSON {path}: {error}") from error


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


def expected_combined_hash(hashes: dict[str, str], content_sha256: str) -> str:
    payload = {key: hashes[key] for key in HASH_INPUT_KEYS}
    payload["cell_content_sha256"] = content_sha256
    return sha256_json(payload)


def expected_cell_hashes(
    cell: dict[str, Any], root_hashes: dict[str, str]
) -> tuple[str, str]:
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


def _json_path(parts: list[Any]) -> str:
    result = "$"
    for part in parts:
        result += f"[{part}]" if isinstance(part, int) else "." + str(part)
    return result


def _resolve_ref(root_schema: dict[str, Any], reference: str) -> dict[str, Any]:
    if not reference.startswith("#/"):
        raise CertifiedImportError(f"only local schema references are supported: {reference}")
    value: Any = root_schema
    for encoded_part in reference[2:].split("/"):
        part = encoded_part.replace("~1", "/").replace("~0", "~")
        if not isinstance(value, dict) or part not in value:
            raise CertifiedImportError(f"unresolvable schema reference: {reference}")
        value = value[part]
    if not isinstance(value, dict):
        raise CertifiedImportError(f"schema reference is not an object: {reference}")
    return value


def _matches_type(value: Any, type_name: str) -> bool:
    if type_name == "object":
        return isinstance(value, dict)
    if type_name == "array":
        return isinstance(value, list)
    if type_name == "string":
        return isinstance(value, str)
    if type_name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if type_name == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if type_name == "boolean":
        return isinstance(value, bool)
    if type_name == "null":
        return value is None
    raise CertifiedImportError(f"unsupported schema type keyword: {type_name}")


def _validate_schema_node(
    value: Any,
    schema: dict[str, Any],
    root_schema: dict[str, Any],
    path: list[Any],
    errors: list[str],
) -> None:
    if "$ref" in schema:
        _validate_schema_node(
            value, _resolve_ref(root_schema, schema["$ref"]), root_schema, path, errors
        )
        return
    location = _json_path(path)
    if "const" in schema and value != schema["const"]:
        errors.append(f"{location}: expected const {schema['const']!r}, got {value!r}")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{location}: value {value!r} is not in {schema['enum']!r}")
    type_keyword = schema.get("type")
    if type_keyword is not None:
        type_names = type_keyword if isinstance(type_keyword, list) else [type_keyword]
        if not any(_matches_type(value, name) for name in type_names):
            errors.append(f"{location}: expected type {type_names!r}")
            return

    if isinstance(value, dict):
        required = schema.get("required", [])
        for key in required:
            if key not in value:
                errors.append(f"{location}: missing required property {key!r}")
        properties = schema.get("properties", {})
        for key, child in value.items():
            if key in properties:
                _validate_schema_node(child, properties[key], root_schema, path + [key], errors)
            else:
                additional = schema.get("additionalProperties", True)
                if additional is False:
                    errors.append(f"{location}: unexpected property {key!r}")
                elif isinstance(additional, dict):
                    _validate_schema_node(child, additional, root_schema, path + [key], errors)
        return

    if isinstance(value, list):
        if len(value) < int(schema.get("minItems", 0)):
            errors.append(f"{location}: too few array items")
        if "maxItems" in schema and len(value) > int(schema["maxItems"]):
            errors.append(f"{location}: too many array items")
        if schema.get("uniqueItems"):
            encoded = [canonical_json_bytes(item) for item in value]
            if len(encoded) != len(set(encoded)):
                errors.append(f"{location}: array items are not unique")
        prefix_items = schema.get("prefixItems", [])
        for index, child_schema in enumerate(prefix_items[: len(value)]):
            _validate_schema_node(value[index], child_schema, root_schema, path + [index], errors)
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            start = len(prefix_items) if prefix_items else 0
            for index in range(start, len(value)):
                _validate_schema_node(value[index], item_schema, root_schema, path + [index], errors)
        return

    if isinstance(value, str):
        if len(value) < int(schema.get("minLength", 0)):
            errors.append(f"{location}: string is too short")
        if "maxLength" in schema and len(value) > int(schema["maxLength"]):
            errors.append(f"{location}: string is too long")
        pattern = schema.get("pattern")
        if pattern is not None and re.search(pattern, value) is None:
            errors.append(f"{location}: string does not match {pattern!r}")
        return

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if not math.isfinite(float(value)):
            errors.append(f"{location}: number must be finite")
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{location}: number is below minimum {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{location}: number exceeds maximum {schema['maximum']}")
        if "exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"]:
            errors.append(
                f"{location}: number must exceed {schema['exclusiveMinimum']}"
            )


def validate_schema_subset(document: Any, schema: dict[str, Any]) -> None:
    errors: list[str] = []
    _validate_schema_node(document, schema, schema, [], errors)
    if errors:
        details = "\n  ".join(errors[:50])
        suffix = f"\n  ... {len(errors) - 50} more" if len(errors) > 50 else ""
        raise CertifiedImportError(
            f"certified JSON schema validation failed ({len(errors)} issues):\n  "
            + details
            + suffix
        )


def validate_hash_contract(document: dict[str, Any]) -> None:
    root_hashes = document["hashes"]
    for cell in document["cells"]:
        for key in HASH_INPUT_KEYS:
            if cell["hashes"][key] != root_hashes[key]:
                raise CertifiedImportError(
                    f"{cell['cell_id']}: compatibility hash {key} differs from root"
                )
        expected_content, expected_combined = expected_cell_hashes(cell, root_hashes)
        if cell["hashes"]["cell_content_sha256"] != expected_content:
            raise CertifiedImportError(f"{cell['cell_id']}: cell content hash mismatch")
        if cell["hashes"]["combined_sha256"] != expected_combined:
            raise CertifiedImportError(f"{cell['cell_id']}: cell combined hash mismatch")
    expected_content, expected_combined = expected_root_hashes(document)
    if root_hashes["cell_content_sha256"] != expected_content:
        raise CertifiedImportError("root cell content hash mismatch")
    if root_hashes["combined_sha256"] != expected_combined:
        raise CertifiedImportError("root combined hash mismatch")


def validate_certified_only(document: dict[str, Any]) -> None:
    if document["schema_version"] != EXPECTED_SCHEMA_VERSION:
        raise CertifiedImportError("unsupported certified schema version")
    if len(document["cells"]) != EXPECTED_CELL_COUNT:
        raise CertifiedImportError(
            f"certified import requires exactly {EXPECTED_CELL_COUNT} cells"
        )
    if len(document["spawn_districts"]) != EXPECTED_DISTRICT_COUNT:
        raise CertifiedImportError(
            f"certified import requires exactly {EXPECTED_DISTRICT_COUNT} districts"
        )

    cell_ids: set[str] = set()
    node_ids: set[str] = set()
    lane_ids: set[str] = set()
    portal_ids: set[str] = set()
    node_records: dict[str, dict[str, Any]] = {}
    lane_records: dict[str, dict[str, Any]] = {}
    portal_records: dict[str, dict[str, Any]] = {}
    component_records: dict[str, dict[str, Any]] = {}
    for component in document["components"]:
        component_id = component["component_id"]
        if component_id in component_records:
            raise CertifiedImportError(f"duplicate component id: {component_id}")
        if component["certified"] is not True:
            raise CertifiedImportError(f"uncertified component rejected: {component_id}")
        component_records[component_id] = component
    for cell in document["cells"]:
        if cell["certified"] is not True:
            raise CertifiedImportError(f"uncertified cell rejected: {cell['cell_id']}")
        if cell["cell_id"] in cell_ids:
            raise CertifiedImportError(f"duplicate cell id: {cell['cell_id']}")
        cell_ids.add(cell["cell_id"])
        for node in cell["nodes"]:
            if node["node_id"] in node_ids:
                raise CertifiedImportError(f"duplicate node id: {node['node_id']}")
            if node["cell_id"] != cell["cell_id"]:
                raise CertifiedImportError(f"node owner mismatch: {node['node_id']}")
            if node["component_id"] not in component_records:
                raise CertifiedImportError(f"node component missing: {node['node_id']}")
            node_ids.add(node["node_id"])
            node_records[node["node_id"]] = node
        for lane in cell["directed_lanes"]:
            if lane["certified"] is not True:
                raise CertifiedImportError(f"uncertified lane rejected: {lane['lane_id']}")
            if lane["lane_id"] in lane_ids:
                raise CertifiedImportError(f"duplicate lane id: {lane['lane_id']}")
            if lane["cell_id"] != cell["cell_id"]:
                raise CertifiedImportError(f"lane owner mismatch: {lane['lane_id']}")
            if lane["component_id"] not in component_records:
                raise CertifiedImportError(f"lane component missing: {lane['lane_id']}")
            if lane["topology_origin"] not in TRUSTED_TOPOLOGY_ORIGINS:
                raise CertifiedImportError(
                    f"untrusted topology origin rejected: {lane['lane_id']}"
                )
            if lane["source_feature_id"] not in cell["source_feature_ids"]:
                raise CertifiedImportError(
                    f"lane source feature missing from cell: {lane['lane_id']}"
                )
            indices = [sample["sample_index"] for sample in lane["ground_samples"]]
            if indices != list(range(len(indices))):
                raise CertifiedImportError(
                    f"noncanonical ground sample indices: {lane['lane_id']}"
                )
            for sample in lane["ground_samples"]:
                if sample["evidence_mask"] != FULL_GROUND_EVIDENCE_MASK:
                    raise CertifiedImportError(
                        f"incomplete ground evidence: {sample['sample_id']}"
                    )
            lane_ids.add(lane["lane_id"])
            lane_records[lane["lane_id"]] = lane
        for portal in cell["portals"]:
            if portal["certified"] is not True:
                raise CertifiedImportError(
                    f"uncertified portal rejected: {portal['portal_id']}"
                )
            if portal["portal_id"] in portal_ids:
                raise CertifiedImportError(f"duplicate portal id: {portal['portal_id']}")
            if portal["local_cell_id"] != cell["cell_id"]:
                raise CertifiedImportError(f"portal owner mismatch: {portal['portal_id']}")
            portal_ids.add(portal["portal_id"])
            portal_records[portal["portal_id"]] = portal

    for lane in lane_records.values():
        if lane["from_node_id"] not in node_ids or lane["to_node_id"] not in node_ids:
            raise CertifiedImportError(f"lane has missing endpoint: {lane['lane_id']}")
        if (
            node_records[lane["from_node_id"]]["component_id"]
            != lane["component_id"]
            or node_records[lane["to_node_id"]]["component_id"]
            != lane["component_id"]
        ):
            raise CertifiedImportError(f"lane crosses component gap: {lane['lane_id']}")
        reverse = lane_records.get(lane["reverse_lane_id"])
        if (
            reverse is None
            or reverse["reverse_lane_id"] != lane["lane_id"]
            or reverse["component_id"] != lane["component_id"]
        ):
            raise CertifiedImportError(f"lane has invalid reverse: {lane['lane_id']}")

    adjacency: dict[str, set[str]] = {node_id: set() for node_id in node_ids}
    for lane in lane_records.values():
        adjacency[lane["from_node_id"]].add(lane["to_node_id"])
    declared_nodes: set[str] = set()
    declared_lanes: set[str] = set()
    for component_id, component in component_records.items():
        component_nodes = set(component["node_ids"])
        component_lanes = set(component["directed_lane_ids"])
        if declared_nodes.intersection(component_nodes) or declared_lanes.intersection(component_lanes):
            raise CertifiedImportError(f"component partition overlaps: {component_id}")
        declared_nodes.update(component_nodes)
        declared_lanes.update(component_lanes)
        if component_nodes != {
            node_id
            for node_id, node in node_records.items()
            if node["component_id"] == component_id
        }:
            raise CertifiedImportError(f"component node membership mismatch: {component_id}")
        derived_lanes = {
            lane_id
            for lane_id, lane in lane_records.items()
            if lane["component_id"] == component_id
        }
        if component_lanes != derived_lanes:
            raise CertifiedImportError(f"component lane membership mismatch: {component_id}")
        pending = [min(component_nodes)]
        reached = {pending[0]}
        while pending:
            current = pending.pop()
            for neighbor in adjacency[current]:
                if neighbor in component_nodes and neighbor not in reached:
                    reached.add(neighbor)
                    pending.append(neighbor)
        if reached != component_nodes:
            raise CertifiedImportError(f"component is not strongly connected: {component_id}")
        derived_cells = sorted({node_records[node_id]["cell_id"] for node_id in component_nodes})
        derived_origins = sorted({lane_records[lane_id]["topology_origin"] for lane_id in component_lanes})
        derived_length = sum(float(lane_records[lane_id]["length_cm"]) for lane_id in component_lanes)
        if component["cell_ids"] != derived_cells:
            raise CertifiedImportError(f"component cell membership mismatch: {component_id}")
        if component["topology_origins"] != derived_origins:
            raise CertifiedImportError(f"component origin evidence mismatch: {component_id}")
        if abs(float(component["directional_lane_length_cm"]) - derived_length) > 0.5:
            raise CertifiedImportError(f"component length evidence mismatch: {component_id}")
    if declared_nodes != node_ids or declared_lanes != lane_ids:
        raise CertifiedImportError("component records do not partition the runtime graph")
    for portal in portal_records.values():
        reverse = portal_records.get(portal["reverse_portal_id"])
        if reverse is None or reverse["reverse_portal_id"] != portal["portal_id"]:
            raise CertifiedImportError(f"portal has invalid reverse: {portal['portal_id']}")
        if portal["remote_cell_id"] not in cell_ids:
            raise CertifiedImportError(f"portal remote cell missing: {portal['portal_id']}")
        if portal["local_node_id"] not in node_ids or portal["remote_node_id"] not in node_ids:
            raise CertifiedImportError(f"portal node missing: {portal['portal_id']}")
        if portal["directed_lane_id"] not in lane_ids:
            raise CertifiedImportError(f"portal lane missing: {portal['portal_id']}")

    districts: set[str] = set()
    population = 0
    for district in document["spawn_districts"]:
        if district["enabled"] is not True:
            raise CertifiedImportError(f"disabled district rejected: {district['district_id']}")
        if district["district_id"] in districts:
            raise CertifiedImportError(f"duplicate district id: {district['district_id']}")
        districts.add(district["district_id"])
        if district["component_id"] not in component_records:
            raise CertifiedImportError(
                f"district component missing: {district['district_id']}"
            )
        if any(identifier not in cell_ids for identifier in district["cell_ids"]):
            raise CertifiedImportError(f"district references missing cell: {district['district_id']}")
        if any(identifier not in node_ids for identifier in district["spawn_node_ids"]):
            raise CertifiedImportError(f"district references missing node: {district['district_id']}")
        if any(identifier not in lane_ids for identifier in district["spawn_lane_ids"]):
            raise CertifiedImportError(f"district references missing lane: {district['district_id']}")
        if any(
            node_records[identifier]["component_id"] != district["component_id"]
            for identifier in district["spawn_node_ids"]
        ) or any(
            lane_records[identifier]["component_id"] != district["component_id"]
            for identifier in district["spawn_lane_ids"]
        ):
            raise CertifiedImportError(
                f"district crosses component gap: {district['district_id']}"
            )
        population += int(district["target_population"])
    if population != EXPECTED_POPULATION:
        raise CertifiedImportError(
            f"certified districts allocate {population}, expected {EXPECTED_POPULATION}"
        )

    provenance = document.get("source_provenance")
    if not isinstance(provenance, dict):
        raise CertifiedImportError("source_provenance is required for asset import")
    if provenance["topology_sha256"] != document["hashes"]["topology_sha256"]:
        raise CertifiedImportError("source provenance topology hash mismatch")


def validate_certified_document(
    document: dict[str, Any], schema: dict[str, Any]
) -> dict[str, Any]:
    validate_schema_subset(document, schema)
    validate_hash_contract(document)
    validate_certified_only(document)
    cells = document["cells"]
    return {
        "schema_valid": True,
        "hashes_valid": True,
        "certified_only": True,
        "source_provenance_valid": True,
        "cell_count": len(cells),
        "node_count": sum(len(cell["nodes"]) for cell in cells),
        "lane_count": sum(len(cell["directed_lanes"]) for cell in cells),
        "ground_sample_count": sum(
            len(lane["ground_samples"])
            for cell in cells
            for lane in cell["directed_lanes"]
        ),
        "portal_count": sum(len(cell["portals"]) for cell in cells),
        "component_count": len(document["components"]),
        "spawn_district_count": len(document["spawn_districts"]),
        "target_population": sum(
            int(district["target_population"])
            for district in document["spawn_districts"]
        ),
    }
