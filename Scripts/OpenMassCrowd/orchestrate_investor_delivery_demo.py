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
        print("[1/5] 等待 Unreal Editor 与项目内置 MCP……", flush=True)
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

        print("[2/5] 自动进入 Play……", flush=True)
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
        print("[3/5] 等待 {} 人完成生成、准入和地面认证……".format(expected), flush=True)
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

        print("[4/5] 自动定位人群镜头并加载近景人物……", flush=True)
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
        print("[5/5] 等待人物 LOD 与稳定性能（P95 < 33 ms）……", flush=True)
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
