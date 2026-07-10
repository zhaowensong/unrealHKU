"""Compatibility entry point for the original PDF-era script name."""

import os

import unreal


script_path = os.path.join(
    os.path.abspath(unreal.Paths.project_dir()),
    "Scripts",
    "SignalRayDemo",
    "build_signal_simulation.py",
)
with open(script_path, "r", encoding="utf-8") as handle:
    code = compile(handle.read(), script_path, "exec")
exec(code, {"__name__": "__main__", "__file__": script_path})
