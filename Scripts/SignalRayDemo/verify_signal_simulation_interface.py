import json
import os

import unreal


frame_path = os.path.join(
    os.path.abspath(unreal.Paths.project_saved_dir()),
    "SignalSimulation",
    "latest-frame.json",
)
response = unreal.SignalSimulationDataLibrary.load_simulation_frame_from_json(frame_path)
if len(response) != 2:
    raise RuntimeError("Unexpected C++ loader response: {!r}".format(response))
frame, error = response
loaded = not bool(error)
result = {
    "loaded": bool(loaded),
    "error": str(error),
    "schema_version": frame.schema_version,
    "frame_id": frame.frame_id,
    "transmitter_count": len(frame.transmitters),
    "segment_count": len(frame.segments),
    "first_transmitter": {
        "id": str(frame.transmitters[0].id),
        "frequency_mhz": frame.transmitters[0].frequency_m_hz,
        "transmit_power_dbm": frame.transmitters[0].transmit_power_dbm,
    },
    "first_segment": {
        "source_id": str(frame.segments[0].source_id),
        "received_power_dbm": frame.segments[0].received_power_dbm,
        "normalized_strength": frame.segments[0].normalized_strength,
        "reflection_hit": frame.segments[0].reflection_hit,
    },
}
if not loaded:
    raise RuntimeError(error)
print(json.dumps(result, ensure_ascii=False, indent=2))
