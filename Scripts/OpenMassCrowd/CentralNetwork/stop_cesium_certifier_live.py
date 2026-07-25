"""Stop the live certifier without discarding its atomic checkpoint."""

import builtins
import json


runner = getattr(
    builtins, "_OPEN_MASS_CENTRAL_CESIUM_CERTIFICATION_RUNNER", None
)
if runner is None or runner.finished:
    report = {"status": "already_stopped"}
else:
    runner.stop()
    runner.save()
    report = {
        "status": "stopped",
        "resolved": len(runner.work["results"]),
        "remaining": len(runner.pending),
    }

print("CENTRAL_CESIUM_CERTIFIER_CONTROL=" + json.dumps(report, sort_keys=True))
