"""Capture the current PIE camera without moving it.

The default output is the close variation shot.  Set
``builtins.OPEN_MASS_EVIDENCE_SHOT_INDEX`` to 6 for the foot-grounding shot or
7 for the final post-restart wide shot before executing this file.
"""

from __future__ import annotations

import builtins
from pathlib import Path

import unreal


SELECTOR_KEY = "OPEN_MASS_EVIDENCE_SHOT_INDEX"
FILENAMES = {
    5: "05_city_sample_29_variants_close.png",
    6: "06_city_sample_cesium_foot_grounding.png",
    7: "07_city_sample_restart_after_6s.png",
}


index = int(getattr(builtins, SELECTOR_KEY, 5))
if index not in FILENAMES:
    raise RuntimeError("Unsupported City Sample evidence shot index: {}".format(index))

output_dir = (
    Path(unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_dir()))
    / "Docs"
    / "Evidence"
    / "OpenMassCrowd"
)
output_dir.mkdir(parents=True, exist_ok=True)
output_path = output_dir / FILENAMES[index]

unreal.AutomationLibrary.take_high_res_screenshot(
    1600,
    900,
    str(output_path),
    camera=None,
    mask_enabled=False,
    capture_hdr=False,
    delay=0.0,
    force_game_view=True,
)
if hasattr(builtins, SELECTOR_KEY):
    delattr(builtins, SELECTOR_KEY)
unreal.log_warning("OPEN_MASS_CITY_SAMPLE_CURRENT_VIEW_REQUESTED={}".format(output_path))
