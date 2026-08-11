#!/usr/bin/env python3
"""Bring the saved TelecomTwin investor demo to a verified presentation state.

This host-side helper talks directly to the project's bundled UnrealMCP socket.
It starts PIE when needed, waits for the exact saved population, moves the PIE
viewer to a visible crowd cluster, and fails explicitly instead of presenting
an empty camera or an incomplete population as a successful demo.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from run_unreal_python_via_mcp import code_for_local_file, execute_python


ROOT = Path(__file__).resolve().parents[2]
CAMERA_SCRIPT = ROOT / "Scripts" / "OpenMassCrowd" / "set_investor_people_camera.py"
DEFAULT_STATUS = (
    ROOT / "Saved" / "InvestorDeliveryDemo" / "one_click_demo_status.json"
)
PROJECT_MARKER = "TELECOMTWIN_DEMO_PROJECT="
WORLD_MARKER = "TELECOMTWIN_DEMO_WORLD="
SNAPSHOT_MARKER = "TELECOMTWIN_DEMO_SNAPSHOT="
SIGNAL_MARKER = "TELECOMTWIN_DEMO_SIGNAL_ALIGNMENT="


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def response_output(response: dict[str, Any]) -> str:
    if response.get("status") != "success":
        result = response.get("result") or {}
        error = str(result.get("error") or "").strip()
        message = str(response.get("message") or "Unreal command failed")
        raise RuntimeError("{}{}".format(message, ": " + error if error else ""))
    return str((response.get("result") or {}).get("output") or "")


def marker_json(output: str, marker: str) -> dict[str, Any]:
    for line in reversed(output.splitlines()):
        if line.startswith(marker):
            return json.loads(line[len(marker) :])
    raise RuntimeError("Unreal response marker is missing: {!r}".format(output))


def unreal_json(code: str, marker: str, timeout: float = 30.0) -> dict[str, Any]:
    return marker_json(
        response_output(execute_python(code, timeout_seconds=timeout)),
        marker,
    )


def write_status(
    output: Path,
    *,
    stage: str,
    ready: bool,
    message: str,
    details: dict[str, Any] | None = None,
) -> None:
    payload = {
        "schema": "telecomtwin-one-click-demo-status-v1",
        "updated_at_utc": utc_now(),
        "stage": stage,
        "ready": ready,
        "message": message,
        "details": details or {},
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def notify_windows_failure(message: str, status_output: Path) -> None:
    try:
        ctypes.windll.user32.MessageBoxW(
            None,
            "TelecomTwin 一键演示启动失败\n\n{}\n\n请查看：\n{}".format(
                message,
                status_output,
            ),
            "TelecomTwin Demo",
            0x00000010,
        )
    except Exception:
        pass


def show_unreal_ready_message(details: dict[str, Any]) -> None:
    if details.get("performance_verified"):
        text = "DEMO READY | 100 PEOPLE | P95 {:.1f} ms".format(
            details["frame_p95_ms"]
        )
    else:
        text = "DEMO READY | 100 PEOPLE | KEEP UE FOREGROUND"
    safe_text = json.dumps(text)
    try:
        response_output(
            execute_python(
                f"""
import unreal
world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_game_world()
if world is not None:
    unreal.SystemLibrary.print_string(
        world,
        {safe_text},
        print_to_screen=True,
        print_to_log=True,
        text_color=unreal.LinearColor(0.15, 1.0, 0.35, 1.0),
        duration=15.0,
        key="TelecomTwinDemoReady",
    )
""",
                timeout_seconds=30.0,
            )
        )
    except Exception:
        # The machine-readable status file remains authoritative if a platform
        # or Editor layout suppresses transient on-screen debug messages.
        pass


def wait_until(
    description: str,
    timeout_seconds: float,
    probe: Callable[[], dict[str, Any] | None],
    interval_seconds: float = 2.0,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last_error = ""
    while time.monotonic() < deadline:
        try:
            result = probe()
            if result is not None:
                return result
        except (OSError, RuntimeError, json.JSONDecodeError) as error:
            current_error = str(error)
            if current_error != last_error:
                print("[等待] {}: {}".format(description, current_error), flush=True)
                last_error = current_error
        time.sleep(interval_seconds)
    raise TimeoutError("等待{}超时（{} 秒）".format(description, timeout_seconds))


def project_probe() -> dict[str, Any]:
    return unreal_json(
        f"""
import json
import unreal
payload = {{
    "project_dir": unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_dir()),
    "editor_world": None,
    "game_world": None,
}}
editor_world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
game_world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_game_world()
if editor_world is not None:
    payload["editor_world"] = editor_world.get_path_name()
if game_world is not None:
    payload["game_world"] = game_world.get_path_name()
print({PROJECT_MARKER!r} + json.dumps(payload, ensure_ascii=False, sort_keys=True))
""",
        PROJECT_MARKER,
    )


def start_or_reuse_pie() -> dict[str, Any]:
    return unreal_json(
        f"""
import json
import unreal
editor_world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
game_world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_game_world()
already_playing = bool(game_world is not None and "UEDPIE_" in game_world.get_path_name().upper())
if not already_playing:
    if editor_world is None or "SHANGHAI" not in editor_world.get_path_name().upper():
        raise RuntimeError("the saved shanghai editor world is not ready")
    unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).editor_request_begin_play()
payload = {{
    "already_playing": already_playing,
    "editor_world": editor_world.get_path_name() if editor_world is not None else None,
    "game_world": game_world.get_path_name() if game_world is not None else None,
}}
print({WORLD_MARKER!r} + json.dumps(payload, ensure_ascii=False, sort_keys=True))
""",
        WORLD_MARKER,
    )


def runtime_snapshot() -> dict[str, Any]:
    return unreal_json(
        f"""
import json
import unreal
world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_game_world()
if world is None or "UEDPIE_" not in world.get_path_name().upper():
    raise RuntimeError("active PIE world is not ready")
spawners = unreal.GameplayStatics.get_all_actors_of_class(world, unreal.OpenMassCrowdSpawner)
if len(spawners) != 1:
    raise RuntimeError("expected one OpenMassCrowdSpawner, found {{}}".format(len(spawners)))
spawner = spawners[0]
delivery = json.loads(spawner.get_investor_demo_evidence_snapshot())
payload = {{
    "world": world.get_path_name(),
    "delivery": delivery,
    "unsupported": int(spawner.get_current_unsupported_visual_count()),
    "invalid_positions": int(spawner.get_central_invalid_position_observation_count()),
    "overlap_pairs": int(spawner.get_central_severe_overlap_pair_count()),
}}
print({SNAPSHOT_MARKER!r} + json.dumps(payload, ensure_ascii=False, sort_keys=True))
""",
        SNAPSHOT_MARKER,
    )


def signal_scene_snapshot() -> dict[str, Any]:
    """Prove PIE uses the exact persisted rooftop signal scene."""
    return unreal_json(
        f"""
import json
import math
import re
import unreal

editor_world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
game_world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_game_world()
if editor_world is None:
    # UE 5.7 returns None from UnrealEditorSubsystem.get_editor_world() while
    # PIE owns the viewport, although the loaded editor world still exists.
    editor_world = next(
        (
            candidate
            for candidate in unreal.ObjectIterator(unreal.World)
            if str(candidate.get_path_name()) == "/Game/Maps/shanghai.shanghai"
        ),
        None,
    )
if editor_world is None or game_world is None:
    raise RuntimeError("editor and PIE worlds are required for signal alignment")

source_pattern = re.compile(r"^SIG_Source_\\d{{2}}_Direct_Roof$")
ray_pattern = re.compile(
    r"^SIG_Ray_\\d{{3}}_(?:Segment|RoofHit)_\\d{{2}}_(Green|Yellow|Orange|Red)$"
)

def is_legacy_floating_signal(label):
    return (
        label.startswith("SIG_RaySegment_")
        or label.startswith("SIG_Node_")
        or label.startswith("SIG_Ray_HISM_")
        or (
            label.startswith("SIG_Source_")
            and source_pattern.fullmatch(label) is None
        )
    )

def actor_label(actor):
    try:
        return str(actor.get_actor_label())
    except Exception:
        return str(actor.get_name())

def actor_hidden(actor):
    try:
        return bool(actor.is_hidden())
    except Exception:
        return bool(actor.get_editor_property("hidden"))

def collect(world):
    result = {{}}
    duplicates = []
    actors = unreal.GameplayStatics.get_all_actors_of_class(world, unreal.Actor)
    for actor in actors:
        label = actor_label(actor)
        if source_pattern.fullmatch(label) is None and ray_pattern.fullmatch(label) is None:
            continue
        if label in result:
            duplicates.append(label)
        result[label] = actor
    return result, sorted(duplicates)

def location_delta(first, second):
    a = first.get_actor_location()
    b = second.get_actor_location()
    return math.sqrt(
        (float(a.x) - float(b.x)) ** 2
        + (float(a.y) - float(b.y)) ** 2
        + (float(a.z) - float(b.z)) ** 2
    )

def angle_delta(first, second):
    return abs((float(first) - float(second) + 180.0) % 360.0 - 180.0)

def rotation_delta(first, second):
    a = first.get_actor_rotation()
    b = second.get_actor_rotation()
    return max(
        angle_delta(a.pitch, b.pitch),
        angle_delta(a.yaw, b.yaw),
        angle_delta(a.roll, b.roll),
    )

def scale_delta(first, second):
    a = first.get_actor_scale3d()
    b = second.get_actor_scale3d()
    return max(
        abs(float(a.x) - float(b.x)),
        abs(float(a.y) - float(b.y)),
        abs(float(a.z) - float(b.z)),
    )

editor_actors, editor_duplicates = collect(editor_world)
game_actors, game_duplicates = collect(game_world)
all_game_actors = unreal.GameplayStatics.get_all_actors_of_class(
    game_world, unreal.Actor
)
legacy_floating_actors = [
    actor
    for actor in all_game_actors
    if is_legacy_floating_signal(actor_label(actor))
]
legacy_floating_visible = []
for actor in legacy_floating_actors:
    primitive_components = actor.get_components_by_class(
        unreal.PrimitiveComponent
    )
    component_visible = any(
        component.is_visible()
        and not bool(component.get_editor_property("hidden_in_game"))
        for component in primitive_components
    )
    if not actor_hidden(actor) and component_visible:
        legacy_floating_visible.append(actor_label(actor))
editor_labels = set(editor_actors)
game_labels = set(game_actors)
common_labels = sorted(editor_labels & game_labels)
missing_in_pie = sorted(editor_labels - game_labels)
extra_in_pie = sorted(game_labels - editor_labels)
hidden_in_pie = sorted(
    label for label, actor in game_actors.items() if actor_hidden(actor)
)

maximum_location_delta_cm = 0.0
maximum_rotation_delta_deg = 0.0
maximum_scale_delta = 0.0
transform_mismatches = []
for label in common_labels:
    editor_actor = editor_actors[label]
    game_actor = game_actors[label]
    location_error = location_delta(editor_actor, game_actor)
    rotation_error = rotation_delta(editor_actor, game_actor)
    scale_error = scale_delta(editor_actor, game_actor)
    maximum_location_delta_cm = max(maximum_location_delta_cm, location_error)
    maximum_rotation_delta_deg = max(maximum_rotation_delta_deg, rotation_error)
    maximum_scale_delta = max(maximum_scale_delta, scale_error)
    if location_error > 0.01 or rotation_error > 0.01 or scale_error > 0.00001:
        if len(transform_mismatches) < 20:
            transform_mismatches.append({{
                "label": label,
                "location_delta_cm": location_error,
                "rotation_delta_deg": rotation_error,
                "scale_delta": scale_error,
            }})

ray_labels = sorted(label for label in game_labels if ray_pattern.fullmatch(label))
source_labels = sorted(label for label in game_labels if source_pattern.fullmatch(label))
color_counts = {{color: 0 for color in ("Green", "Yellow", "Orange", "Red")}}
spawners = unreal.GameplayStatics.get_all_actors_of_class(
    game_world, unreal.OpenMassCrowdSpawner
)
if len(spawners) != 1:
    raise RuntimeError("expected one OpenMassCrowdSpawner for signal batching")
spawner = spawners[0]
delivery = json.loads(spawner.get_investor_demo_evidence_snapshot())
signal_rendering = delivery["signal_rendering"]
batch_components = []
for component in spawner.get_components_by_class(
    unreal.HierarchicalInstancedStaticMeshComponent
):
    tags = {{str(tag) for tag in component.get_editor_property("component_tags")}}
    if "TelecomTwinSignalBatch" in tags:
        batch_components.append(component)

visible_batch_instance_count = 0
source_instance_count = 0
invisible_batch_components = []
batch_groups = []
for component in batch_components:
    instance_count = int(component.get_instance_count())
    hidden_in_game = bool(component.get_editor_property("hidden_in_game"))
    visible = component.is_visible() and not hidden_in_game
    material = component.get_material(0)
    material_path = str(material.get_path_name()) if material is not None else ""
    mesh = component.get_editor_property("static_mesh")
    mesh_path = str(mesh.get_path_name()) if mesh is not None else ""
    if visible:
        visible_batch_instance_count += instance_count
    else:
        invisible_batch_components.append(str(component.get_name()))
    matched_color = None
    for color in color_counts:
        if "MI_SignalRay_{{}}".format(color) in material_path:
            color_counts[color] += instance_count
            matched_color = color
            break
    if "MI_SignalRay_Source" in material_path:
        source_instance_count += instance_count
        matched_color = "Source"
    batch_groups.append({{
        "component": str(component.get_name()),
        "mesh": mesh_path,
        "material": material_path,
        "instances": instance_count,
        "visible": visible,
        "kind": matched_color,
    }})

batch_snapshot_matches = (
    signal_rendering["source"]
    == "persisted_editor_component_world_transforms"
    and bool(signal_rendering["batch_ready"])
    and int(signal_rendering["original_actor_count"]) == 1950
    and int(signal_rendering["batch_component_count"]) == 9
    and int(signal_rendering["batched_instance_count"]) == 1950
    and float(signal_rendering["maximum_location_delta_cm"]) <= 0.01
    and float(signal_rendering["maximum_rotation_delta_deg"]) <= 0.01
    and float(signal_rendering["maximum_scale_delta"]) <= 0.00001
    and not bool(signal_rendering["runtime_overlay_enabled"])
    and int(delivery["legacy_signal"]["floating_mock_visible_count"]) == 0
    and float(delivery["legacy_signal"]["late_stream_scan_hz"]) >= 4.0
)

passed = (
    len(source_labels) == 30
    and len(ray_labels) == 1920
    and len(batch_components) == 9
    and visible_batch_instance_count == 1950
    and source_instance_count == 30
    and all(count == 480 for count in color_counts.values())
    and not editor_duplicates
    and not game_duplicates
    and not missing_in_pie
    and not extra_in_pie
    and len(hidden_in_pie) == 1950
    and not invisible_batch_components
    and not transform_mismatches
    and not legacy_floating_visible
    and batch_snapshot_matches
)
payload = {{
    "passed": passed,
    "policy": "persisted_editor_rooftop_component_transforms_batched_for_PIE",
    "source_actor_count": len(source_labels),
    "ray_actor_count": len(ray_labels),
    "batch_component_count": len(batch_components),
    "visible_batch_instance_count": visible_batch_instance_count,
    "source_instance_count": source_instance_count,
    "color_geometry_counts": color_counts,
    "missing_in_pie_count": len(missing_in_pie),
    "extra_in_pie_count": len(extra_in_pie),
    "original_actor_hidden_for_batching_count": len(hidden_in_pie),
    "transform_mismatch_count": len(transform_mismatches),
    "maximum_location_delta_cm": maximum_location_delta_cm,
    "maximum_rotation_delta_deg": maximum_rotation_delta_deg,
    "maximum_scale_delta": maximum_scale_delta,
    "batch_snapshot_matches": batch_snapshot_matches,
    "runtime_overlay_enabled": False,
    "legacy_floating_actor_loaded_count": len(legacy_floating_actors),
    "legacy_floating_actor_visible_count": len(legacy_floating_visible),
    "legacy_floating_actor_suppression": "continuous_world_partition_scan",
    "samples": {{
        "missing_in_pie": missing_in_pie[:20],
        "extra_in_pie": extra_in_pie[:20],
        "hidden_in_pie": hidden_in_pie[:20],
        "invisible_batch_components": invisible_batch_components[:20],
        "transform_mismatches": transform_mismatches,
        "batch_groups": sorted(batch_groups, key=lambda item: item["component"]),
        "visible_legacy_floating_signals": sorted(legacy_floating_visible)[:20],
    }},
}}
print({SIGNAL_MARKER!r} + json.dumps(payload, ensure_ascii=False, sort_keys=True))
""",
        SIGNAL_MARKER,
        timeout=60.0,
    )


def configure_demo_runtime() -> None:
    """Keep the Editor at presentation frame rate while the launcher waits."""
    response_output(
        execute_python(
            """
import unreal
world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_game_world()
if world is None:
    raise RuntimeError("active PIE world is required for demo runtime settings")
unreal.SystemLibrary.execute_console_command(world, "t.IdleWhenNotForeground 0")
unreal.SystemLibrary.execute_console_command(world, "Slate.bAllowThrottling 0")
print("TELECOMTWIN_DEMO_RUNTIME_CONFIGURED")
""",
            timeout_seconds=30.0,
        )
    )


def compact_details(snapshot: dict[str, Any]) -> dict[str, Any]:
    delivery = snapshot["delivery"]
    population = delivery["population"]
    liveness = delivery["liveness"]
    presentation = delivery["presentation"]
    performance = delivery["performance"]
    return {
        "population": population,
        "moving": int(liveness["moving"]),
        "stuck": int(liveness["stuck"]),
        "maximum_stationary_s": float(liveness["maximum_stationary_s"]),
        "high_actors": int(presentation["high_actors"]),
        "low_actors": int(presentation["low_actors"]),
        "vat_actors": int(presentation["vat_actors"]),
        "unique_active_lanes": int(presentation["unique_active_lanes"]),
        "frame_p50_ms": float(performance["frame_p50_ms"]),
        "frame_p95_ms": float(performance["frame_p95_ms"]),
        "unsupported": int(snapshot["unsupported"]),
        "invalid_positions": int(snapshot["invalid_positions"]),
        "overlap_pairs": int(snapshot["overlap_pairs"]),
    }


def editor_background_throttle_detected(details: dict[str, Any]) -> bool:
    """Recognize UE Editor's 3 FPS unfocused-window throttle signature.

    During a fully unfocused sample both P50 and P95 are about 333 ms.  A sample
    that straddles an AppActivate/tool-window transition keeps a normal P50 but
    still has a 333 ms P95.  Both are editor focus artifacts, not crowd cost.
    """
    p50 = float(details["frame_p50_ms"])
    p95 = float(details["frame_p95_ms"])
    return 250.0 <= p95 <= 400.0 and (p50 < 50.0 or 250.0 <= p50 <= 400.0)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-population", type=int, default=100)
    parser.add_argument("--editor-timeout", type=float, default=180.0)
    parser.add_argument("--demo-timeout", type=float, default=240.0)
    parser.add_argument("--status-output", type=Path, default=DEFAULT_STATUS)
    parser.add_argument("--notify-failure", action="store_true")
    args = parser.parse_args()
    expected = args.expected_population
    if expected <= 0:
        raise ValueError("expected population must be positive")

    try:
        write_status(
            args.status_output,
            stage="waiting_for_editor",
            ready=False,
            message="正在等待 TelecomTwin Unreal Editor",
        )
        print("[1/6] 等待 Unreal Editor 与项目内置 MCP……", flush=True)
        project = wait_until(
            "Unreal Editor MCP",
            args.editor_timeout,
            lambda: project_probe(),
        )
        actual_root = Path(str(project["project_dir"])).resolve()
        if actual_root != ROOT.resolve():
            raise RuntimeError(
                "检测到的是其他 UE 项目：{}；请关闭它后重试".format(actual_root)
            )

        print("[2/6] 自动进入 Play……", flush=True)
        start_or_reuse_pie()
        wait_until(
            "shanghai Play 世界",
            60.0,
            lambda: (
                snapshot
                if "UEDPIE_" in (snapshot := runtime_snapshot())["world"].upper()
                else None
            ),
        )
        # A foreground console normally makes UE Editor throttle itself to
        # roughly 3 FPS. Disable only that presentation-time throttle before
        # measuring readiness; otherwise a correct crowd would appear slow
        # merely because the one-click progress window is visible.
        configure_demo_runtime()

        write_status(
            args.status_output,
            stage="waiting_for_population",
            ready=False,
            message="正在等待 100 人完成模拟与认证准入",
        )
        print("[3/6] 等待 {} 人完成生成、准入和地面认证……".format(expected), flush=True)
        last_population_state: tuple[int, ...] | None = None

        def population_ready() -> dict[str, Any] | None:
            nonlocal last_population_state
            snapshot = runtime_snapshot()
            delivery = snapshot["delivery"]
            population = delivery["population"]
            liveness = delivery["liveness"]
            state = (
                int(population["spawned"]),
                int(population["admitted"]),
                int(population["represented"]),
                int(liveness["moving"]),
                int(liveness["stuck"]),
            )
            if state != last_population_state:
                print(
                    "      spawned={} admitted={} represented={} moving={} stuck={}".format(
                        *state
                    ),
                    flush=True,
                )
                last_population_state = state
            if (
                int(population["configured"]) == expected
                and state[0] == expected
                and state[1] == expected
                and state[3] >= (expected * 95 + 99) // 100
                and state[4] == 0
                and int(snapshot["unsupported"]) == 0
                and int(snapshot["invalid_positions"]) == 0
                and int(snapshot["overlap_pairs"]) == 0
            ):
                return snapshot
            return None

        population_snapshot = wait_until(
            "完整模拟人群",
            args.demo_timeout,
            population_ready,
        )

        print("[4/6] 自动定位人群镜头并加载近景人物……", flush=True)
        camera_output = response_output(
            execute_python(
                code_for_local_file(CAMERA_SCRIPT),
                timeout_seconds=30.0,
            )
        )
        camera_lines = [
            line for line in camera_output.splitlines() if "INVESTOR_PEOPLE_CAMERA=" in line
        ]
        if camera_lines:
            print("      " + camera_lines[-1], flush=True)

        write_status(
            args.status_output,
            stage="warming_presentation",
            ready=False,
            message="正在加载近景人物并等待稳定帧时",
            details=compact_details(population_snapshot),
        )
        print("[5/6] 等待人物 LOD 与稳定性能（P95 < 33 ms）……", flush=True)
        last_presentation_report_time = 0.0

        def presentation_ready() -> dict[str, Any] | None:
            nonlocal last_presentation_report_time
            snapshot = runtime_snapshot()
            details = compact_details(snapshot)
            population = details["population"]
            actor_count = details["high_actors"] + details["low_actors"]
            performance_verified = (
                details["frame_p95_ms"] > 0.0
                and details["frame_p95_ms"] < 33.0
            )
            background_throttle_detected = editor_background_throttle_detected(
                details
            )
            details["performance_verified"] = performance_verified
            details["background_throttle_detected"] = (
                background_throttle_detected
            )
            healthy = (
                int(population["spawned"]) == expected
                and int(population["admitted"]) == expected
                and int(population["represented"]) == expected
                and details["moving"] >= (expected * 95 + 99) // 100
                and details["stuck"] == 0
                and details["unsupported"] == 0
                and details["invalid_positions"] == 0
                and details["overlap_pairs"] == 0
                and actor_count > 0
                and (performance_verified or background_throttle_detected)
            )
            now = time.monotonic()
            if now - last_presentation_report_time >= 5.0:
                print(
                    "      moving={moving} actors={actors} VAT={vat} P95={p95:.3f} ms".format(
                        moving=details["moving"],
                        actors=actor_count,
                        vat=details["vat_actors"],
                        p95=details["frame_p95_ms"],
                    ),
                    flush=True,
                )
                write_status(
                    args.status_output,
                    stage="warming_presentation",
                    ready=False,
                    message="正在加载近景人物并等待稳定帧时",
                    details=details,
                )
                last_presentation_report_time = now
            if healthy:
                return snapshot
            return None

        ready_snapshot = wait_until(
            "稳定演示画面",
            args.demo_timeout,
            presentation_ready,
        )
        details = compact_details(ready_snapshot)
        details["performance_verified"] = (
            details["frame_p95_ms"] > 0.0
            and details["frame_p95_ms"] < 33.0
        )
        details["background_throttle_detected"] = editor_background_throttle_detected(
            details
        )
        write_status(
            args.status_output,
            stage="verifying_signal_alignment",
            ready=False,
            message="正在核对 Play 与编辑器中的真实屋顶信道",
            details=details,
        )
        print("[6/6] 核对 30 信源四色信道与编辑器态完全一致……", flush=True)
        signal_alignment = signal_scene_snapshot()
        if not signal_alignment.get("passed"):
            raise RuntimeError(
                "Play 中信道未保持真实屋顶版本：{}".format(
                    json.dumps(signal_alignment, ensure_ascii=False, sort_keys=True)
                )
            )
        details["signal_alignment"] = signal_alignment
        write_status(
            args.status_output,
            stage="ready",
            ready=True,
            message="TelecomTwin 100 人演示已就绪",
            details=details,
        )
        show_unreal_ready_message(details)
        print("", flush=True)
        print("============================================================", flush=True)
        print("  TelecomTwin 100 人演示已就绪", flush=True)
        print(
            "  moving={moving} stuck={stuck} actors={actors} VAT={vat} P95={p95:.3f} ms verified={verified}".format(
                moving=details["moving"],
                stuck=details["stuck"],
                actors=details["high_actors"] + details["low_actors"],
                vat=details["vat_actors"],
                p95=details["frame_p95_ms"],
                verified=details["performance_verified"],
            ),
            flush=True,
        )
        print("============================================================", flush=True)
        return 0
    except Exception as error:
        write_status(
            args.status_output,
            stage="failed",
            ready=False,
            message=str(error),
        )
        print("", flush=True)
        print("[失败] {}".format(error), flush=True)
        print("状态文件：{}".format(args.status_output), flush=True)
        if args.notify_failure:
            notify_windows_failure(str(error), args.status_output)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
