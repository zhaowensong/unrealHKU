# Hong Kong Crowd Demo

## Result

The experiment adds a small UE 5.7-native moving crowd to the existing Hong Kong TelecomTwin map. It does not modify the telecom-ray implementation.

- Demo population: 6 pedestrians (configurable on `HK_Crowd_Spawner`)
- Runtime controller: `ADetourCrowdAIController`
- Ground source: visible `CesiumGltfPrimitiveComponent` triangle collision
- Ground acceptance: nine local Cesium samples, roof normal Z >= 0.72, height spread <= 18 cm
- Selected roof center: `(-89800.00, 215800.00, 6723.19)`
- Verified roof spread: 17.70 cm
- Local navigation bounds: `1200 x 840 x 600` cm

## Verification

The final restart test reported:

- `HK_CROWD_READY requested=6 spawned=6`
- All six actors remained on the roof and their runtime world positions changed between two samples five seconds apart.
- No `is stuck` or `failed to move` warning appeared in the final six-person test.

Evidence:

- `Saved/Screenshots/WindowsEditor/HighresScreenshot00002.png`
- `Saved/Logs/TelecomTwin.log` (`HK_CROWD_SETUP_OK`, `HK_CROWD_READY`, `HK_CROWD_RESTART_A`, `HK_CROWD_RESTART_B`)

## Re-run

1. Open `/Game/Maps/shanghai` and place the viewport near the Hong Kong Convention and Exhibition Centre roof so the Cesium tiles are loaded.
2. Run `Scripts/HongKongCrowd/setup_crowd_demo.py` from the Unreal Python console.
3. Wait for `HK_CROWD_NAV_REBUILD_OK`, then use Play In Editor.

The setup script refuses to create the demo when it cannot prove a sufficiently flat, collision-backed Cesium roof patch.

## Rollback and scaling

- Git rollback tag: `rollback/pre-hk-crowd-2026-07-13`
- Experiment branch: `experiment/hk-crowd-ue57`
- The level actors can be removed by label: `HK_Crowd_Spawner` and `HK_Crowd_NavBounds`.

Scale in stages. First enlarge a collision-verified walkable area, then increase the population. Do not raise `GroundTolerance` merely to obtain more spawn points because that would reintroduce visible floating.
