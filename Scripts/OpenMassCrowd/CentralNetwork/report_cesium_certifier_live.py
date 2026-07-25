"""Print bounded live state for the Central Cesium certification runner."""

import builtins
import json


runner = getattr(
    builtins, "_OPEN_MASS_CENTRAL_CESIUM_CERTIFICATION_RUNNER", None
)
if runner is None:
    report = {"status": "not_started"}
else:
    bounds = runner.current_cell_bounds
    manual_overlap_count = 0
    if bounds is not None:
        cell_min = bounds["min"]
        cell_max = bounds["max"]
        manual_overlap_count = sum(
            1
            for record in runner.adapter.components
            if record["max"][0] >= float(cell_min[0])
            and record["min"][0] <= float(cell_max[0])
            and record["max"][1] >= float(cell_min[1])
            and record["min"][1] <= float(cell_max[1])
        )
    report = {
        "status": "finished" if runner.finished else "running",
        "cell_id": runner.current_cell,
        "wait_ticks": runner.wait_ticks,
        "stable_ticks": runner.stable_ticks,
        "cell_query_component_count": runner.adapter.signature_component_count,
        "all_query_component_count": len(runner.adapter.components),
        "manual_cell_overlap_count": manual_overlap_count,
        "cell_bounds": bounds,
        "resolved": len(runner.work["results"]),
        "remaining": len(runner.pending),
        "candidate_active": runner.current_generator is not None,
        "candidate_streaming_restarts": runner.candidate_streaming_restarts,
    }

print("CENTRAL_CESIUM_CERTIFIER_LIVE=" + json.dumps(report, sort_keys=True))
