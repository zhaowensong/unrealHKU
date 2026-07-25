"""Make persisted signal visualization geometry non-physical in the editor map.

This helper intentionally does not save the level. Run it with PIE stopped,
review its machine-readable PASS result, and then save the current level.
"""

from __future__ import annotations

import json
import re

import unreal


SOURCE_PATTERN = re.compile(r"^SIG_Source_\d{2}_Direct_Roof$")
RAY_PATTERN = re.compile(
    r"^SIG_Ray_\d{3}_(?:Segment|RoofHit)_\d{2}_"
    r"(Green|Yellow|Orange|Red)$"
)
EXPECTED_SOURCE_COUNT = 30
EXPECTED_RAY_GEOMETRY_COUNT = 1920
EXPECTED_PER_COLOR_COUNT = 480
EXPECTED_COMPONENT_COUNT = EXPECTED_SOURCE_COUNT + EXPECTED_RAY_GEOMETRY_COUNT


def _call_if_available(target, name: str, *args):
    method = getattr(target, name, None)
    if not callable(method):
        return False
    method(*args)
    return True


def _overlap_events(component) -> bool | None:
    getter = getattr(component, "get_generate_overlap_events", None)
    if callable(getter):
        return bool(getter())
    try:
        return bool(component.get_editor_property("generate_overlap_events"))
    except Exception:
        return None


def _disable_overlap_events(component) -> bool:
    setter = getattr(component, "set_generate_overlap_events", None)
    if callable(setter):
        setter(False)
        return True
    try:
        component.set_editor_property("generate_overlap_events", False)
        return True
    except Exception:
        return False


def _package_name(actor) -> str:
    for getter_name in ("get_package", "get_outermost"):
        getter = getattr(actor, getter_name, None)
        if not callable(getter):
            continue
        package = getter()
        if package is not None:
            return str(package.get_name())
    return str(actor.get_path_name()).split(":", 1)[0]


def _label_kind(label: str) -> tuple[str | None, str | None]:
    if SOURCE_PATTERN.fullmatch(label):
        return "source", None
    match = RAY_PATTERN.fullmatch(label)
    if match is not None:
        return "ray", match.group(1)
    return None, None


def main() -> None:
    editor_subsystem = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem)
    if editor_subsystem.get_game_world() is not None:
        raise RuntimeError("stop PIE before editing persisted signal actors")
    world = editor_subsystem.get_editor_world()
    if world is None:
        raise RuntimeError("editor world is unavailable")

    actor_subsystem = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    if actor_subsystem is None:
        raise RuntimeError("editor actor subsystem is unavailable")
    actors = list(actor_subsystem.get_all_level_actors())
    matched = []
    for actor in actors:
        label = str(actor.get_actor_label())
        kind, color = _label_kind(label)
        if kind is not None:
            matched.append((label, kind, color, actor))
    matched.sort(key=lambda item: item[0])

    source_count = sum(kind == "source" for _label, kind, _color, _actor in matched)
    ray_count = sum(kind == "ray" for _label, kind, _color, _actor in matched)
    color_counts = {
        color: sum(item_color == color for _label, _kind, item_color, _actor in matched)
        for color in ("Green", "Yellow", "Orange", "Red")
    }
    labels = [label for label, _kind, _color, _actor in matched]

    component_records = []
    verification_components = []
    changed_component_count = 0
    dirty_packages: set[str] = set()
    unexpected_component_counts = []
    overlap_api_unavailable_count = 0
    for label, kind, color, actor in matched:
        components = list(
            actor.get_components_by_class(unreal.PrimitiveComponent)
        )
        if len(components) != 1:
            unexpected_component_counts.append(
                {"label": label, "primitive_component_count": len(components)}
            )
        for component in components:
            verification_components.append((label, component))
            query_before = bool(component.is_query_collision_enabled())
            physics_before = bool(component.is_physics_collision_enabled())
            overlap_before = _overlap_events(component)
            needs_change = query_before or physics_before or overlap_before is True
            if needs_change:
                _call_if_available(actor, "modify")
                _call_if_available(component, "modify")
                component.set_collision_enabled(unreal.CollisionEnabled.NO_COLLISION)
                if not _disable_overlap_events(component):
                    overlap_api_unavailable_count += 1
                _call_if_available(component, "mark_package_dirty")
                _call_if_available(actor, "mark_package_dirty")
                dirty_packages.add(_package_name(actor))
                changed_component_count += 1
            component_records.append(
                {
                    "label": label,
                    "kind": kind,
                    "color": color,
                    "component": str(component.get_path_name()),
                    "query_before": query_before,
                    "physics_before": physics_before,
                    "overlap_before": overlap_before,
                }
            )

    residual_query = []
    residual_physics = []
    residual_overlap = []
    overlap_state_unavailable_count = 0
    for label, component in verification_components:
        if component.is_query_collision_enabled():
            residual_query.append(label)
        if component.is_physics_collision_enabled():
            residual_physics.append(label)
        overlap_after = _overlap_events(component)
        if overlap_after is True:
            residual_overlap.append(label)
        elif overlap_after is None:
            overlap_state_unavailable_count += 1

    errors = []
    if source_count != EXPECTED_SOURCE_COUNT:
        errors.append(
            f"source_count={source_count} expected={EXPECTED_SOURCE_COUNT}"
        )
    if ray_count != EXPECTED_RAY_GEOMETRY_COUNT:
        errors.append(
            f"ray_geometry_count={ray_count} expected={EXPECTED_RAY_GEOMETRY_COUNT}"
        )
    for color, count in color_counts.items():
        if count != EXPECTED_PER_COLOR_COUNT:
            errors.append(
                f"{color.lower()}_count={count} expected={EXPECTED_PER_COLOR_COUNT}"
            )
    if len(set(labels)) != len(labels):
        errors.append("duplicate matched actor labels")
    if len(component_records) != EXPECTED_COMPONENT_COUNT:
        errors.append(
            "primitive_component_count={} expected={}".format(
                len(component_records), EXPECTED_COMPONENT_COUNT
            )
        )
    if unexpected_component_counts:
        errors.append("matched actor does not have exactly one PrimitiveComponent")
    if residual_query:
        errors.append(f"query_collision_enabled={len(residual_query)} expected=0")
    if residual_physics:
        errors.append(f"physics_collision_enabled={len(residual_physics)} expected=0")
    if residual_overlap:
        errors.append(f"overlap_events_enabled={len(residual_overlap)} expected=0")

    report = {
        "schema_version": 1,
        "status": "FAIL" if errors else "PASS",
        "world": str(world.get_path_name()),
        "expected": {
            "sources": EXPECTED_SOURCE_COUNT,
            "ray_geometries": EXPECTED_RAY_GEOMETRY_COUNT,
            "per_color": EXPECTED_PER_COLOR_COUNT,
            "primitive_components": EXPECTED_COMPONENT_COUNT,
        },
        "observed": {
            "sources": source_count,
            "ray_geometries": ray_count,
            "per_color": color_counts,
            "primitive_components": len(component_records),
            "unique_labels": len(set(labels)),
        },
        "changed_component_count": changed_component_count,
        "already_compliant_component_count": (
            len(component_records) - changed_component_count
        ),
        "before": {
            "query_collision_enabled": sum(
                bool(record["query_before"]) for record in component_records
            ),
            "physics_collision_enabled": sum(
                bool(record["physics_before"]) for record in component_records
            ),
            "overlap_events_enabled": sum(
                record["overlap_before"] is True for record in component_records
            ),
        },
        "dirty_package_count": len(dirty_packages),
        "dirty_package_examples": sorted(dirty_packages)[:10],
        "automatic_save": False,
        "residual": {
            "query_collision_enabled": len(residual_query),
            "physics_collision_enabled": len(residual_physics),
            "overlap_events_enabled": len(residual_overlap),
            "overlap_state_unavailable": overlap_state_unavailable_count,
            "overlap_api_unavailable": overlap_api_unavailable_count,
        },
        "unexpected_component_counts": unexpected_component_counts[:20],
        "errors": errors,
    }
    print("SIGNAL_VISUAL_NO_COLLISION=" + json.dumps(report, sort_keys=True))
    if errors:
        raise RuntimeError("; ".join(errors))


if __name__ == "__main__":
    main()
