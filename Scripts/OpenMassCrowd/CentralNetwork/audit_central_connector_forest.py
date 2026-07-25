#!/usr/bin/env python3
"""Select the minimal collision-accepted connector forest for human review.

Generated connector probes are deliberately excluded from the runtime graph.
This read-only audit starts from the formally trusted OSM/semantic components,
then applies only collision-accepted generated connectors in deterministic
shortest-first order.  A connector is essential when it joins two components
which have not already been joined by an earlier accepted connector.  The
result is a small review set rather than a visually unreadable union of every
accepted proximity probe.
"""

from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_PROJECTED = SCRIPT_DIR / "Data/central_pedestrian_source_unreal.json"
DEFAULT_CORRECTIONS = SCRIPT_DIR / "central_manual_corrections.json"
DEFAULT_WORK = SCRIPT_DIR / "Data/central_network_certification_working.json"
DEFAULT_REPORT = (
    SCRIPT_DIR.parents[2] / "Saved/Reports/central_connector_forest_latest.json"
)
TRUSTED_ORIGINS = frozenset({"openstreetmap", "osm-semantic-recovery"})


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def polyline_length(samples: list[dict[str, Any]]) -> float:
    total = 0.0
    for first, second in zip(samples, samples[1:]):
        a = first["center_position"]
        b = second["center_position"]
        total += math.sqrt(sum((float(b[i]) - float(a[i])) ** 2 for i in range(3)))
    return total


class DisjointSet:
    def __init__(self) -> None:
        self.parent: dict[str, str] = {}

    def find(self, value: str) -> str:
        self.parent.setdefault(value, value)
        if self.parent[value] != value:
            self.parent[value] = self.find(self.parent[value])
        return self.parent[value]

    def union(self, first: str, second: str) -> bool:
        first_root = self.find(first)
        second_root = self.find(second)
        if first_root == second_root:
            return False
        if first_root > second_root:
            first_root, second_root = second_root, first_root
        self.parent[second_root] = first_root
        return True


def candidate_cells(candidate: dict[str, Any]) -> list[str]:
    values = {str(candidate.get("source_cell_id", ""))}
    for value in (
        candidate.get("candidate_id", ""),
        candidate.get("source_feature_id", ""),
    ):
        text = str(value)
        for row in range(2):
            for column in range(3):
                cell_id = f"central-r{row}-c{column}"
                if cell_id in text:
                    values.add(cell_id)
    return sorted(value for value in values if value.startswith("central-r"))


def build_report(work: dict[str, Any]) -> dict[str, Any]:
    results = work["results"]
    trusted = [
        result
        for result in results.values()
        if result.get("status") == "accepted"
        and result.get("topology_origin") in TRUSTED_ORIGINS
    ]

    trusted_set = DisjointSet()
    trusted_nodes: set[str] = set()
    for result in trusted:
        first = str(result["from_point_id"])
        second = str(result["to_point_id"])
        trusted_nodes.update((first, second))
        trusted_set.union(first, second)
    trusted_component_by_node = {
        node_id: trusted_set.find(node_id) for node_id in trusted_nodes
    }
    trusted_component_count = len(set(trusted_component_by_node.values()))

    generated_by_id = {
        candidate["candidate_id"]: candidate
        for candidate in work.get("generated_connector_candidates", [])
    }
    accepted: list[dict[str, Any]] = []
    for candidate_id, candidate in generated_by_id.items():
        result = results.get(candidate_id) or {}
        if result.get("status") != "accepted":
            continue
        first = str(candidate["from_point_id"])
        second = str(candidate["to_point_id"])
        if first not in trusted_component_by_node or second not in trusted_component_by_node:
            continue
        accepted.append(
            {
                "candidate_id": candidate_id,
                "from_point_id": first,
                "to_point_id": second,
                "from_component": trusted_component_by_node[first],
                "to_component": trusted_component_by_node[second],
                "length_cm": round(polyline_length(result.get("samples", [])), 3),
                "cell_ids": candidate_cells(candidate),
                "semantic_level_key": str(
                    (candidate.get("tags") or {}).get("semantic_level_key", "")
                ),
            }
        )

    accepted.sort(key=lambda item: (item["length_cm"], item["candidate_id"]))
    component_set = DisjointSet()
    for component_id in set(trusted_component_by_node.values()):
        component_set.find(component_id)
    essential: list[dict[str, Any]] = []
    redundant: list[str] = []
    already_internal: list[str] = []
    for item in accepted:
        if item["from_component"] == item["to_component"]:
            already_internal.append(item["candidate_id"])
        elif component_set.union(item["from_component"], item["to_component"]):
            essential.append(item)
        else:
            redundant.append(item["candidate_id"])

    component_members: dict[str, set[str]] = defaultdict(set)
    for component_id in set(trusted_component_by_node.values()):
        component_members[component_set.find(component_id)].add(component_id)
    merged_sizes = sorted(
        (len(component_ids) for component_ids in component_members.values()), reverse=True
    )
    essential_cells = sorted(
        {cell_id for item in essential for cell_id in item["cell_ids"]}
    )
    return {
        "schema_version": 1,
        "read_only": True,
        "trust_policy": {
            "formally_trusted_origins": sorted(TRUSTED_ORIGINS),
            "generated_connectors_formally_trusted": False,
            "selection": "deterministic-shortest-first-component-forest",
        },
        "trusted_component_count_before": trusted_component_count,
        "accepted_generated_connector_count": len(accepted),
        "essential_connector_count": len(essential),
        "redundant_connector_count": len(redundant),
        "already_internal_connector_count": len(already_internal),
        "projected_component_count_after_essential": len(component_members),
        "largest_projected_component_trusted_component_count": (
            merged_sizes[0] if merged_sizes else 0
        ),
        "essential_cell_ids": essential_cells,
        "essential_connectors": essential,
        "redundant_connector_ids": redundant,
        "already_internal_connector_ids": already_internal,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, default=DEFAULT_WORK)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = build_report(load_json(args.work))
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        "CENTRAL_CONNECTOR_FOREST="
        + json.dumps(
            {
                "trusted_component_count_before": report[
                    "trusted_component_count_before"
                ],
                "accepted_generated_connector_count": report[
                    "accepted_generated_connector_count"
                ],
                "essential_connector_count": report["essential_connector_count"],
                "projected_component_count_after_essential": report[
                    "projected_component_count_after_essential"
                ],
                "largest_projected_component_trusted_component_count": report[
                    "largest_projected_component_trusted_component_count"
                ],
                "report": str(args.report.resolve()),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
