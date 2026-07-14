# TelecomTwin

TelecomTwin is an Unreal Engine 5.7 telecom digital-twin prototype for Hong Kong. It combines Cesium photorealistic 3D tiles with base-station, user, crowd, venue, and signal-ray visualizations.

## Main components

- `Content/Maps/shanghai.umap`: current Hong Kong scene (the legacy map name is retained for compatibility).
- `Scripts/SignalRayDemo/build_signal_ray_demo.py`: Unreal-side signal-ray generator.
- `Plugins/OpenMassCrowd`: isolated UE 5.7 Mass/ZoneGraph pedestrian demo.
- `run_signal_ray_demo.py`: TCP client that asks UnrealMCP to execute the generator.
- `Plugins/UnrealMCP`: vendored UnrealMCP editor plugin source.
- `Config`: Unreal project and map settings.

## Requirements

- Unreal Engine 5.7
- Cesium for Unreal 2.25.x
- VaRest
- Python Editor Script Plugin
- UnrealMCP server running on port `13377`

The Google Photorealistic 3D Tiles actor is intentionally not committed because its binary asset contains a local API key. Add the tileset from the Cesium panel after cloning, using your own Cesium ion or Google Maps credentials, and keep credentials out of project assets.

## Run the current signal-ray demo

Open `TelecomTwin.uproject`, start the UnrealMCP control panel, then run:

```powershell
python .\run_signal_ray_demo.py
```

The checked-in `.venv` is intentionally excluded. Create a fresh virtual environment if one is desired; the TCP client itself only uses the Python standard library.

## Run the Hong Kong crowd demo

Open `TelecomTwin.uproject`, wait for the nearby Cesium tiles, then click Play or
press `Alt+P`. The saved `shanghai` level automatically creates 30 Mass crowd
entities; no setup script is needed for normal startup. See
[`Docs/OpenMassCrowd_UE57_HongKong_Demo.md`](Docs/OpenMassCrowd_UE57_HongKong_Demo.md)
for implementation details, screenshots, verification, limitations, and rollback.

## Repository policy

Generated Unreal output, local Cesium caches, virtual environments, logs, and plugin binaries are excluded. Project content assets and source code are versioned so scene and implementation changes can be reviewed together.

## Vendored dependency

`Plugins/UnrealMCP` was vendored from `https://github.com/kvick-games/UnrealMCP.git` at commit `f989d0e` and is maintained inside this repository for project-specific fixes.
