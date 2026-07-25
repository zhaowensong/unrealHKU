"""Persist one approved Central crowd population gate in ``shanghai``.

Run only with PIE stopped. The script fails closed unless the editor is on
``/Game/Maps/shanghai.shanghai`` and that level contains exactly one
``OpenMassCrowdSpawner`` already configured for the Central certified cache.
It changes only ``central_population_gate`` and ``population_count``.

Examples::

    python Scripts/OpenMassCrowd/run_unreal_python_via_mcp.py \
      --file Scripts/OpenMassCrowd/promote_central_crowd_gate.py \
      --script-arg=100

    python Scripts/OpenMassCrowd/run_unreal_python_via_mcp.py \
      --file Scripts/OpenMassCrowd/promote_central_crowd_gate.py \
      --script-arg=30 --script-arg=--allow-downgrade

Upward transitions must be sequential (30 -> 100 -> 200 -> 300). Repeating
the current target is an idempotent no-op. A lower gate requires the explicit
``--allow-downgrade`` switch. Runtime gate PASS evidence remains the caller's
responsibility to collect; every upward promotion also validates and hashes the
previous gate's stable runtime PASS report before changing the map.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import stat
from datetime import datetime, timezone
from pathlib import Path

try:
    import unreal
except ModuleNotFoundError:  # Allows deterministic host-side self-tests.
    unreal = None


MAP_OBJECT_PATH = "/Game/Maps/shanghai.shanghai"
REPORT_PREFIX = "central_crowd_gate_promotion"
RUNTIME_REPORT_PREFIX = "central_crowd_gate_runtime"
RUNTIME_REPORT_SCHEMA_VERSION = 4
MINIMUM_RUNTIME_SAMPLE_SECONDS = 60.0
REQUIRED_COLLISION_CHECK_NAME = (
    "no_severe_overlap_below_20cm_during_steady_60s_window"
)
SEVERE_OVERLAP_THRESHOLD_CM = 20.0
APPROVED_GATES = (30, 100, 200, 300)
GATE_ENUM_NAMES = {
    30: "GATE30",
    100: "GATE100",
    200: "GATE200",
    300: "GATE300",
}
CENTRAL_MODE_ENUM_NAME = "CENTRAL_CERTIFIED_CACHE"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_gate(value) -> int:
    try:
        gate = int(value)
    except (TypeError, ValueError) as error:
        raise argparse.ArgumentTypeError(
            "gate must be one of {}".format(APPROVED_GATES)
        ) from error
    if gate not in APPROVED_GATES:
        raise argparse.ArgumentTypeError(
            "gate must be one of {}".format(APPROVED_GATES)
        )
    return gate


def classify_transition(
    current_gate: int, target_gate: int, allow_downgrade: bool = False
) -> str:
    """Validate a gate transition and return its deterministic action name."""
    if current_gate not in APPROVED_GATES:
        raise RuntimeError("current Central gate is not approved: {}".format(current_gate))
    if target_gate not in APPROVED_GATES:
        raise RuntimeError("target Central gate is not approved: {}".format(target_gate))
    if current_gate == target_gate:
        return "same_gate"
    if target_gate < current_gate:
        if not allow_downgrade:
            raise RuntimeError(
                "refusing Central gate downgrade {} -> {}; pass "
                "--allow-downgrade explicitly".format(current_gate, target_gate)
            )
        return "downgrade"

    current_index = APPROVED_GATES.index(current_gate)
    expected_gate = APPROVED_GATES[current_index + 1]
    if target_gate != expected_gate:
        raise RuntimeError(
            "refusing to skip an evidence gate: {} may promote only to {}, "
            "not {}".format(current_gate, expected_gate, target_gate)
        )
    return "promotion"


def require_unreal():
    if unreal is None:
        raise RuntimeError(
            "this operation must run inside Unreal Editor; use "
            "run_unreal_python_via_mcp.py"
        )


def require_type(name: str):
    value = getattr(unreal, name, None)
    if value is None:
        raise RuntimeError(
            "reflected type {} is unavailable; rebuild OpenMassCrowd and "
            "restart Unreal Editor".format(name)
        )
    return value


def enum_value(type_name: str, value_name: str):
    enum_type = require_type(type_name)
    value = getattr(enum_type, value_name, None)
    if value is None:
        raise RuntimeError("enum {}.{} is unavailable".format(type_name, value_name))
    return value


def enum_gate_number(value) -> int:
    enum_type = require_type("OpenMassCrowdCentralPopulationGate")
    for gate, name in GATE_ENUM_NAMES.items():
        if value == getattr(enum_type, name, None):
            return gate
    raise RuntimeError("spawner has an unapproved Central gate: {}".format(value))


def require_pie_stopped():
    subsystem_type = getattr(unreal, "UnrealEditorSubsystem", None)
    if subsystem_type is None:
        raise RuntimeError("cannot prove PIE is stopped: UnrealEditorSubsystem is absent")
    subsystem = unreal.get_editor_subsystem(subsystem_type)
    getter = getattr(subsystem, "get_game_world", None)
    if subsystem is None or getter is None:
        raise RuntimeError("cannot prove PIE is stopped: get_game_world is unavailable")
    game_world = getter()
    if game_world is not None:
        raise RuntimeError(
            "PIE or Simulate is active (game world={}); stop it before changing "
            "the Central gate".format(game_world.get_path_name())
        )


def require_shanghai_world():
    world = unreal.EditorLevelLibrary.get_editor_world()
    world_path = world.get_path_name() if world is not None else None
    if world_path != MAP_OBJECT_PATH:
        raise RuntimeError(
            "open {} before changing the Central gate; current={}".format(
                MAP_OBJECT_PATH, world_path
            )
        )
    return world


def find_exactly_one_central_spawner():
    actor_subsystem = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    spawner_class = require_type("OpenMassCrowdSpawner")
    spawners = sorted(
        (
            actor
            for actor in actor_subsystem.get_all_level_actors()
            if isinstance(actor, spawner_class)
        ),
        key=lambda actor: actor.get_path_name(),
    )
    paths = [actor.get_path_name() for actor in spawners]
    if len(spawners) != 1:
        raise RuntimeError(
            "{} requires exactly one OpenMassCrowdSpawner; found {}: {}".format(
                MAP_OBJECT_PATH, len(spawners), paths
            )
        )

    spawner = spawners[0]
    expected_mode = enum_value("OpenMassCrowdNetworkMode", CENTRAL_MODE_ENUM_NAME)
    actual_mode = spawner.get_editor_property("network_mode")
    if actual_mode != expected_mode:
        raise RuntimeError(
            "the unique spawner is not a Central certified-cache spawner: {} "
            "mode={}".format(spawner.get_path_name(), actual_mode)
        )
    if spawner.get_editor_property("central_network_asset") is None:
        raise RuntimeError("the unique Central spawner has no central_network_asset")
    return spawner


def project_report_dir() -> Path:
    return Path(
        unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_saved_dir())
    ) / "Reports" / "OpenMassCrowd"


def validate_previous_runtime_payload(
    payload, current_gate: int, expected_asset: str, engine_version: str
):
    """Fail closed unless a runtime report proves the gate being promoted."""
    final_snapshot = payload.get("runtime_snapshot_final") or {}
    network_audit = payload.get("network_asset_audit") or {}
    diagnostics = payload.get("diagnostics") or {}
    sampling = payload.get("sampling") or {}
    checks = payload.get("checks") or {}
    collision_contract = payload.get("collision_contract") or {}
    required_collision_check = checks.get(REQUIRED_COLLISION_CHECK_NAME) or {}
    failed_required_checks = sorted(
        name
        for name, check in checks.items()
        if check.get("required") is True and check.get("passed") is not True
    )
    mismatches = []
    expected_pairs = (
        (
            "schema_version",
            payload.get("schema_version"),
            RUNTIME_REPORT_SCHEMA_VERSION,
        ),
        ("overall_passed", payload.get("overall_passed"), True),
        ("completion_reason", payload.get("completion_reason"), "completed"),
        ("target_gate", payload.get("target_gate"), current_gate),
        (
            "canonical_world_package",
            final_snapshot.get("canonical_world_package"),
            "/Game/Maps/shanghai",
        ),
        ("network_asset", network_audit.get("asset"), expected_asset),
        ("engine_version", payload.get("engine_version"), engine_version),
        ("diagnostic_error_count", diagnostics.get("error_count"), 0),
        ("required_failed_checks", payload.get("required_failed_checks"), []),
        (
            "getter_contract_missing",
            (payload.get("getter_contract") or {}).get("missing"),
            [],
        ),
        (
            "required_collision_check.required",
            required_collision_check.get("required"),
            True,
        ),
        (
            "required_collision_check.passed",
            required_collision_check.get("passed"),
            True,
        ),
        (
            "collision_contract.severe_overlap_threshold_cm",
            collision_contract.get("severe_overlap_threshold_cm"),
            SEVERE_OVERLAP_THRESHOLD_CM,
        ),
        (
            "collision_contract.planned_spawn_quality_is_advisory",
            collision_contract.get("planned_spawn_quality_is_advisory"),
            True,
        ),
    )
    for name, actual, expected in expected_pairs:
        if actual != expected:
            mismatches.append(
                "{} expected={!r} actual={!r}".format(name, expected, actual)
            )
    for name in (
        "required_seconds",
        "elapsed_game_seconds",
        "elapsed_wall_seconds",
    ):
        actual = sampling.get(name)
        if (
            isinstance(actual, bool)
            or not isinstance(actual, (int, float))
            or actual < MINIMUM_RUNTIME_SAMPLE_SECONDS
        ):
            mismatches.append(
                "sampling.{} expected>={!r} actual={!r}".format(
                    name, MINIMUM_RUNTIME_SAMPLE_SECONDS, actual
                )
            )
    if failed_required_checks:
        mismatches.append(
            "required checks failed: {}".format(failed_required_checks)
        )
    if mismatches:
        raise RuntimeError(
            "previous Gate{} runtime evidence is not promotable: {}".format(
                current_gate, "; ".join(mismatches)
            )
        )
    return {
        "overall_passed": True,
        "completion_reason": "completed",
        "target_gate": current_gate,
        "canonical_world_package": "/Game/Maps/shanghai",
        "network_asset": expected_asset,
        "engine_version": engine_version,
        "generated_utc": payload.get("generated_utc"),
        "required_sample_seconds": sampling.get("required_seconds"),
        "elapsed_game_seconds": sampling.get("elapsed_game_seconds"),
        "elapsed_wall_seconds": sampling.get("elapsed_wall_seconds"),
        "required_check_count": sum(
            1 for check in checks.values() if check.get("required") is True
        ),
    }


def require_previous_runtime_pass(current_gate: int, expected_asset: str):
    report_path = project_report_dir() / (
        "{}_gate{}_latest.json".format(RUNTIME_REPORT_PREFIX, current_gate)
    )
    if not report_path.is_file():
        raise RuntimeError(
            "Gate{} runtime PASS report is missing: {}".format(
                current_gate, report_path
            )
        )
    raw = report_path.read_bytes()
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError(
            "Gate{} runtime report is not valid UTF-8 JSON: {}".format(
                current_gate, report_path
            )
        ) from error
    result = validate_previous_runtime_payload(
        payload,
        current_gate,
        expected_asset,
        unreal.SystemLibrary.get_engine_version(),
    )
    result["path"] = str(report_path)
    result["sha256"] = hashlib.sha256(raw).hexdigest()
    return result


def _atomic_write_json(path: Path, payload) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    for candidate in (path, temporary):
        if candidate.exists() and not (candidate.stat().st_mode & stat.S_IWRITE):
            os.chmod(candidate, candidate.stat().st_mode | stat.S_IWRITE)
    text = (
        json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def ensure_map_file_writable() -> tuple[Path, bool]:
    """Clear only a OneDrive-restored read-only bit before saving shanghai."""
    map_file = Path(
        unreal.Paths.convert_relative_path_to_full(
            unreal.Paths.project_content_dir()
        )
    ) / "Maps" / "shanghai.umap"
    if not map_file.is_file():
        raise RuntimeError("shanghai map package is missing: {}".format(map_file))
    was_read_only = not (map_file.stat().st_mode & stat.S_IWRITE)
    if was_read_only:
        os.chmod(map_file, map_file.stat().st_mode | stat.S_IWRITE)
    return map_file, was_read_only


def write_report(payload, target_gate: int):
    report_dir = project_report_dir()
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    timestamped = report_dir / (
        "{}_gate{}_{}.json".format(REPORT_PREFIX, target_gate, stamp)
    )
    gate_latest = report_dir / (
        "{}_gate{}_latest.json".format(REPORT_PREFIX, target_gate)
    )
    latest = report_dir / (REPORT_PREFIX + "_latest.json")
    for path in (timestamped, gate_latest, latest):
        _atomic_write_json(path, payload)
    return timestamped, gate_latest, latest


def promote_gate(target_gate: int, allow_downgrade: bool = False):
    require_unreal()
    payload = {
        "schema_version": 1,
        "generated_utc": utc_now(),
        "status": "IN_PROGRESS",
        "map": MAP_OBJECT_PATH,
        "requested_gate": target_gate,
        "approved_gates": list(APPROVED_GATES),
        "allow_downgrade": bool(allow_downgrade),
        "pie_must_be_stopped": True,
        "runtime_pass_must_be_verified_before_next_promotion": True,
        "changed_properties": [],
        "save_performed": False,
        "idempotent_noop": False,
    }

    try:
        require_pie_stopped()
        payload["pie_stopped"] = True
        world = require_shanghai_world()
        payload["editor_world"] = world.get_path_name()
        spawner = find_exactly_one_central_spawner()
        spawner_path = spawner.get_path_name()
        payload["spawner"] = spawner_path
        payload["spawner_count"] = 1
        payload["network_mode"] = CENTRAL_MODE_ENUM_NAME
        network_asset = spawner.get_editor_property("central_network_asset")
        payload["network_asset"] = network_asset.get_path_name()

        current_gate_value = spawner.get_editor_property(
            "central_population_gate"
        )
        current_gate = enum_gate_number(current_gate_value)
        current_population = int(spawner.get_editor_property("population_count"))
        if current_population != current_gate:
            raise RuntimeError(
                "refusing to promote inconsistent Central configuration: "
                "gate={} population_count={}".format(
                    current_gate, current_population
                )
            )
        transition = classify_transition(
            current_gate, target_gate, allow_downgrade=allow_downgrade
        )
        payload["before"] = {
            "gate": current_gate,
            "population_count": current_population,
        }
        payload["transition"] = transition
        if transition == "promotion":
            payload["previous_gate_runtime_evidence"] = (
                require_previous_runtime_pass(
                    current_gate, network_asset.get_path_name()
                )
            )
        else:
            payload["previous_gate_runtime_evidence"] = {
                "required": False,
                "reason": transition,
            }

        target_gate_value = enum_value(
            "OpenMassCrowdCentralPopulationGate", GATE_ENUM_NAMES[target_gate]
        )
        changed_properties = []
        if current_gate_value != target_gate_value:
            changed_properties.append("central_population_gate")
        if current_population != target_gate:
            changed_properties.append("population_count")

        if changed_properties:
            try:
                map_file, read_only_cleared = ensure_map_file_writable()
                payload["map_file"] = str(map_file)
                payload["map_read_only_cleared"] = read_only_cleared
                with unreal.ScopedEditorTransaction(
                    "Set Central crowd Gate{}".format(target_gate)
                ):
                    if "central_population_gate" in changed_properties:
                        spawner.set_editor_property(
                            "central_population_gate", target_gate_value
                        )
                    if "population_count" in changed_properties:
                        spawner.set_editor_property("population_count", target_gate)

                if enum_gate_number(
                    spawner.get_editor_property("central_population_gate")
                ) != target_gate or int(
                    spawner.get_editor_property("population_count")
                ) != target_gate:
                    raise RuntimeError("Central gate properties did not round-trip")
                if not unreal.EditorLevelLibrary.save_current_level():
                    raise RuntimeError("failed to save /Game/Maps/shanghai")
                payload["save_performed"] = True
            except Exception:
                # Do not leave a failed save attempt promoted in editor memory.
                spawner.set_editor_property(
                    "central_population_gate", current_gate_value
                )
                spawner.set_editor_property("population_count", current_population)
                payload["rolled_back_in_memory"] = True
                raise
        else:
            payload["idempotent_noop"] = True

        # Re-resolve after save and prove the unique actor and two target values.
        verified_spawner = find_exactly_one_central_spawner()
        if verified_spawner.get_path_name() != spawner_path:
            raise RuntimeError("Central spawner identity changed during gate update")
        verified_gate = enum_gate_number(
            verified_spawner.get_editor_property("central_population_gate")
        )
        verified_population = int(
            verified_spawner.get_editor_property("population_count")
        )
        if verified_gate != target_gate or verified_population != target_gate:
            raise RuntimeError(
                "saved Central gate mismatch: gate={} population={} target={}".format(
                    verified_gate, verified_population, target_gate
                )
            )

        payload["changed_properties"] = changed_properties
        payload["after"] = {
            "gate": verified_gate,
            "population_count": verified_population,
        }
        payload["status"] = "PASS"
        payload["generated_utc"] = utc_now()
        paths = write_report(payload, target_gate)
        marker = {
            "status": payload["status"],
            "requested_gate": target_gate,
            "transition": transition,
            "changed_properties": changed_properties,
            "idempotent_noop": payload["idempotent_noop"],
            "timestamped_report": str(paths[0]),
            "gate_latest_report": str(paths[1]),
            "latest_report": str(paths[2]),
        }
        unreal.log_warning(
            "OPEN_MASS_CENTRAL_GATE_PROMOTION="
            + json.dumps(marker, ensure_ascii=False, sort_keys=True)
        )
        return payload
    except Exception as error:
        payload["status"] = "FAIL"
        payload["generated_utc"] = utc_now()
        payload["error"] = "{}: {}".format(type(error).__name__, error)
        try:
            paths = write_report(payload, target_gate)
            payload["failure_reports"] = [str(path) for path in paths]
        except Exception as report_error:
            payload["report_write_error"] = "{}: {}".format(
                type(report_error).__name__, report_error
            )
        unreal.log_error(
            "OPEN_MASS_CENTRAL_GATE_PROMOTION_ERROR="
            + json.dumps(payload, ensure_ascii=False, sort_keys=True)
        )
        raise


def run_self_test() -> int:
    assert parse_gate("30") == 30
    assert parse_gate(100) == 100
    for rejected in (0, 31, 99, 301, "Gate100", None):
        try:
            parse_gate(rejected)
        except argparse.ArgumentTypeError:
            pass
        else:
            raise AssertionError("parse_gate accepted {!r}".format(rejected))

    assert classify_transition(30, 30) == "same_gate"
    assert classify_transition(30, 100) == "promotion"
    assert classify_transition(100, 200) == "promotion"
    assert classify_transition(200, 300) == "promotion"
    assert classify_transition(300, 30, allow_downgrade=True) == "downgrade"
    for current, target, allow_downgrade in (
        (30, 200, False),
        (30, 300, False),
        (100, 300, False),
        (300, 200, False),
    ):
        try:
            classify_transition(current, target, allow_downgrade)
        except RuntimeError:
            pass
        else:
            raise AssertionError(
                "unsafe transition accepted: {} -> {}".format(current, target)
            )

    synthetic_runtime_report = {
        "schema_version": RUNTIME_REPORT_SCHEMA_VERSION,
        "overall_passed": True,
        "completion_reason": "completed",
        "target_gate": 30,
        "engine_version": "test-engine",
        "generated_utc": "2026-07-19T00:00:00+00:00",
        "required_failed_checks": [],
        "getter_contract": {"missing": []},
        "runtime_snapshot_final": {
            "canonical_world_package": "/Game/Maps/shanghai"
        },
        "network_asset_audit": {"asset": "/Game/Test/DA.DA"},
        "diagnostics": {"error_count": 0},
        "sampling": {
            "required_seconds": MINIMUM_RUNTIME_SAMPLE_SECONDS,
            "elapsed_game_seconds": MINIMUM_RUNTIME_SAMPLE_SECONDS + 0.1,
            "elapsed_wall_seconds": MINIMUM_RUNTIME_SAMPLE_SECONDS + 0.1,
        },
        "collision_contract": {
            "severe_overlap_threshold_cm": SEVERE_OVERLAP_THRESHOLD_CM,
            "planned_spawn_quality_target_cm": 55.0,
            "planned_spawn_quality_is_advisory": True,
        },
        "checks": {
            "required_ok": {"required": True, "passed": True},
            REQUIRED_COLLISION_CHECK_NAME: {
                "required": True,
                "passed": True,
            },
            "advisory_only": {"required": False, "passed": False},
        },
    }
    validated = validate_previous_runtime_payload(
        synthetic_runtime_report, 30, "/Game/Test/DA.DA", "test-engine"
    )
    assert validated["target_gate"] == 30
    assert validated["required_check_count"] == 2
    rejected_runtime_report = dict(synthetic_runtime_report)
    rejected_runtime_report["overall_passed"] = False
    try:
        validate_previous_runtime_payload(
            rejected_runtime_report, 30, "/Game/Test/DA.DA", "test-engine"
        )
    except RuntimeError:
        pass
    else:
        raise AssertionError("failed runtime evidence was accepted for promotion")

    legacy_collision_report = copy.deepcopy(synthetic_runtime_report)
    legacy_collision_report["checks"].pop(REQUIRED_COLLISION_CHECK_NAME)
    legacy_collision_report["checks"][
        "no_body_overlap_below_55cm_during_steady_60s_window"
    ] = {"required": True, "passed": True}
    try:
        validate_previous_runtime_payload(
            legacy_collision_report, 30, "/Game/Test/DA.DA", "test-engine"
        )
    except RuntimeError:
        pass
    else:
        raise AssertionError("legacy 55 cm collision check was accepted for promotion")

    short_runtime_report = dict(synthetic_runtime_report)
    short_runtime_report["sampling"] = {
        "required_seconds": 15.0,
        "elapsed_game_seconds": 15.2,
        "elapsed_wall_seconds": 15.2,
    }
    try:
        validate_previous_runtime_payload(
            short_runtime_report, 30, "/Game/Test/DA.DA", "test-engine"
        )
    except RuntimeError:
        pass
    else:
        raise AssertionError("short runtime evidence was accepted for promotion")

    print(
        json.dumps(
            {
                "status": "PASS",
                "approved_gates": list(APPROVED_GATES),
                "upward_transitions": [[30, 100], [100, 200], [200, 300]],
                "idempotent_same_gate": True,
                "downgrade_requires_explicit_switch": True,
                "promotion_requires_previous_runtime_pass": True,
                "runtime_report_schema_version": RUNTIME_REPORT_SCHEMA_VERSION,
                "minimum_runtime_sample_seconds": MINIMUM_RUNTIME_SAMPLE_SECONDS,
                "required_collision_check_name": REQUIRED_COLLISION_CHECK_NAME,
                "severe_overlap_threshold_cm": SEVERE_OVERLAP_THRESHOLD_CM,
                "legacy_55cm_collision_check_rejected": True,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Persist one approved Central crowd population gate"
    )
    parser.add_argument("gate", nargs="?", type=parse_gate)
    parser.add_argument("--allow-downgrade", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)
    if not args.self_test and args.gate is None:
        parser.error("gate is required unless --self-test is used")
    if args.self_test and args.gate is not None:
        parser.error("gate cannot be supplied with --self-test")
    return args


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.self_test:
        return run_self_test()
    promote_gate(args.gate, allow_downgrade=args.allow_downgrade)
    return 0


if __name__ == "__main__":
    # Do not raise SystemExit on the UnrealMCP thread: a successful UE script
    # must return normally so the bridge reports status=success.
    main()
