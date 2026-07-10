import argparse
import json
import os
import subprocess
import sys
import winreg
from pathlib import Path


PROJECT_FILE = "TelecomTwin.uproject"
DEFAULT_MAP = "/Game/Maps/shanghai"
DEFAULT_SCRIPT = Path("Scripts") / "SignalRayDemo" / "build_signal_ray_demo.py"


def project_root() -> Path:
    return Path(__file__).resolve().parent


def engine_association(project_file: Path) -> str | None:
    try:
        data = json.loads(project_file.read_text(encoding="utf-8-sig"))
    except Exception:
        return None
    value = data.get("EngineAssociation")
    return str(value) if value else None


def registry_engine_dirs(association: str | None) -> list[Path]:
    roots: list[Path] = []

    keys = [
        (winreg.HKEY_CURRENT_USER, r"Software\Epic Games\Unreal Engine\Builds"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\EpicGames\Unreal Engine"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\EpicGames\Unreal Engine"),
    ]

    for hive, key_name in keys:
        try:
            with winreg.OpenKey(hive, key_name) as key:
                index = 0
                while True:
                    try:
                        name, value, _ = winreg.EnumValue(key, index)
                    except OSError:
                        break
                    index += 1
                    if association and association not in {name, Path(str(value)).name.replace("UE_", "")}:
                        continue
                    roots.append(Path(str(value)))

                index = 0
                while True:
                    try:
                        subkey_name = winreg.EnumKey(key, index)
                    except OSError:
                        break
                    index += 1
                    if association and subkey_name != association:
                        continue
                    try:
                        with winreg.OpenKey(key, subkey_name) as subkey:
                            install_dir, _ = winreg.QueryValueEx(subkey, "InstalledDirectory")
                            roots.append(Path(str(install_dir)))
                    except OSError:
                        pass
        except OSError:
            pass

    return roots


def common_engine_dirs(association: str | None) -> list[Path]:
    names = []
    if association:
        names.extend([f"UE_{association}", f"UE{association}", association])

    bases = [
        Path(os.environ.get("UE_ROOT", "")),
        Path(os.environ.get("UE5_ROOT", "")),
        Path(os.environ.get("UNREAL_ENGINE_ROOT", "")),
        Path(r"C:\Program Files\Epic Games"),
        Path(r"D:\Program Files\Epic Games"),
        Path(r"D:\Epic Games"),
        Path(r"D:\UE"),
        Path(r"D:\Unreal"),
    ]

    roots: list[Path] = []
    for base in bases:
        if not str(base):
            continue
        if (base / "Engine" / "Binaries" / "Win64").exists():
            roots.append(base)
        for name in names:
            roots.append(base / name)
    return roots


def editor_exe_from_root(root: Path, mode: str) -> Path:
    exe_name = "UnrealEditor-Cmd.exe" if mode == "cmd" else "UnrealEditor.exe"
    return root / "Engine" / "Binaries" / "Win64" / exe_name


def find_editor(project_file: Path, mode: str, explicit_editor: str | None) -> Path | None:
    if explicit_editor:
        return Path(explicit_editor).expanduser().resolve()

    env_editor = os.environ.get("UE_EDITOR_EXE")
    if env_editor:
        return Path(env_editor).expanduser().resolve()

    association = engine_association(project_file)
    candidates = registry_engine_dirs(association) + common_engine_dirs(association)
    for root in candidates:
        exe = editor_exe_from_root(root, mode)
        if exe.exists():
            return exe.resolve()

    return None


def build_command(editor: Path, project_file: Path, map_name: str, script: Path, mode: str) -> list[str]:
    if mode == "cmd":
        return [
            str(editor),
            str(project_file),
            map_name,
            "-run=pythonscript",
            f"-script={script}",
            "-unattended",
            "-nop4",
        ]

    return [
        str(editor),
        str(project_file),
        map_name,
        f"-ExecutePythonScript={script}",
    ]


def main() -> int:
    root = project_root()
    project_file = root / PROJECT_FILE

    parser = argparse.ArgumentParser(
        description="Launch Unreal Editor directly and run the Signal Ray Demo build script without Unreal MCP."
    )
    parser.add_argument("--editor", help="Full path to UnrealEditor.exe or UnrealEditor-Cmd.exe.")
    parser.add_argument("--map", default=DEFAULT_MAP, help=f"Map to load. Default: {DEFAULT_MAP}")
    parser.add_argument("--script", default=str(root / DEFAULT_SCRIPT), help="Unreal Python script to execute.")
    parser.add_argument(
        "--mode",
        choices=["editor", "cmd"],
        default="editor",
        help="editor opens the normal Unreal Editor; cmd uses UnrealEditor-Cmd. Default: editor",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print the command without launching Unreal.")
    args = parser.parse_args()

    if not project_file.exists():
        print(f"Project file not found: {project_file}", file=sys.stderr)
        return 2

    script = Path(args.script).expanduser().resolve()
    if not script.exists():
        print(f"Script not found: {script}", file=sys.stderr)
        return 2

    editor = find_editor(project_file, args.mode, args.editor)
    if not editor or not editor.exists():
        print(
            "Could not find Unreal Editor automatically.\n"
            "Pass it explicitly, for example:\n"
            r'  python run_signal_ray_demo_direct.py --editor "C:\Program Files\Epic Games\UE_5.7\Engine\Binaries\Win64\UnrealEditor.exe"',
            file=sys.stderr,
        )
        return 3

    command = build_command(editor, project_file.resolve(), args.map, script, args.mode)
    print("Launching Unreal command:")
    print(subprocess.list2cmdline(command))

    if args.dry_run:
        return 0

    completed = subprocess.run(command, cwd=str(root))
    return int(completed.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
