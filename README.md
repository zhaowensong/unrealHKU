# TelecomTwin

TelecomTwin is an Unreal Engine 5.7 telecom digital-twin prototype for Hong Kong. It combines Cesium photorealistic 3D tiles with base-station, user, crowd, venue, and signal-ray visualizations.

## Architecture

- `Content/Maps/shanghai.umap`: current Hong Kong scene (the legacy map name is retained for compatibility).
- `ASignalRayManager`: the single non-spatially-loaded owner of three ray HISM renderers, one reflection-node HISM, and one hidden collision-proxy HISM.
- `Scripts/SignalRayDemo/build_signal_simulation.py`: Unreal integration for the deterministic multi-source propagation engine.
- `Scripts/SignalRayDemo/signal_simulation.py`: recursive reflection, free-space path loss, reflection loss, and zero-penetration validation.
- `Config/SignalSimulation/default_scenario.json`: propagation settings and stable building proxy definitions.
- `Config/SignalSimulation/signal-frame.schema.json`: formal `telecom-twin.signal-frame/1.0` data contract.
- `run_signal_ray_demo.py`: authenticated loopback client that asks UnrealMCP to rebuild the simulation.
- `Plugins/UnrealMCP`: vendored UnrealMCP editor plugin source.
- `Config`: Unreal project and map settings.

## Requirements

- Unreal Engine 5.7
- Cesium for Unreal 2.25.x
- VaRest
- Python Editor Script Plugin
- UnrealMCP on local port `13377`

The Google Photorealistic 3D Tiles actor is intentionally not committed because its binary asset contains a local API key. Add the tileset from the Cesium panel after cloning, using your own Cesium ion or Google Maps credentials, and keep credentials out of project assets.

## Run the signal simulation

Open `TelecomTwin.uproject`. The hardened MCP server starts automatically on `127.0.0.1:13377`, creates a local token at `Saved/MCP/auth-token.txt`, and accepts Python files only from the project `Scripts` directory. Then run:

```powershell
python .\run_signal_ray_demo.py
```

The default scenario uses the existing `BS_00_top` through `BS_11_top` actors. A successful run currently builds 820 batched path segments, 592 reflection nodes, and 18 stable collision proxies without spawning per-segment actors.

Useful verification commands:

```powershell
python .\run_signal_ray_demo.py --script .\Scripts\SignalRayDemo\verify_signal_ray_environment.py
python .\run_signal_ray_demo.py --script .\Scripts\SignalRayDemo\verify_signal_simulation_interface.py
python .\run_signal_ray_demo.py --script .\Scripts\SignalRayDemo\demonstrate_pdf_requirements.py
```

The PDF requirement report distinguishes deterministic proxy-layer validation from visual alignment to streamed Cesium tile surfaces. The proxy layer is deterministic and penetration-free; exact Cesium surface alignment still depends on collision data being available from the streamed tiles.

The generated formal frame is written to `Saved/SignalSimulation/latest-frame.json` and is intentionally excluded from Git.

The checked-in `.venv` is intentionally excluded. Create a fresh virtual environment if one is desired; the TCP client itself only uses the Python standard library.

## Repository policy

Generated Unreal output, local MCP tokens, local Cesium caches, virtual environments, logs, and plugin binaries are excluded. Project content assets and source code are versioned so scene and implementation changes can be reviewed together.

## Vendored dependency

`Plugins/UnrealMCP` was vendored from `https://github.com/kvick-games/UnrealMCP.git` at commit `f989d0e` and is maintained inside this repository for project-specific fixes.
