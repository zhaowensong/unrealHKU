# Central pedestrian-network tools

The files in this directory keep three trust levels separate:

1. `Data/central_pedestrian_source.json` is reviewable WGS84 topology.
2. `Data/central_pedestrian_source_unreal.json` is a Cesium-georeferenced candidate mirror. Its Z is not certified.
3. `Data/central_network_certified.json` is the only input permitted to create the runtime UE DataAsset.

Source and projected-candidate JSON can never be substituted for the certified file.

## Rebuild and validate source data

```powershell
python Scripts/OpenMassCrowd/CentralNetwork/central_osm_pipeline.py build
python Scripts/OpenMassCrowd/CentralNetwork/central_osm_pipeline.py validate Scripts/OpenMassCrowd/CentralNetwork/Data/central_pedestrian_source.json
python Scripts/OpenMassCrowd/CentralNetwork/central_osm_pipeline.py self-test
```

To reproduce without network access, pass `--input-osm` with the checked-in raw Overpass JSON. `--fixture` is only a tool self-test and must not be promoted as Central data.

## Project WGS84 candidates into Unreal

Open `/Game/Maps/shanghai`, then run:

```powershell
python Scripts/OpenMassCrowd/run_unreal_python_via_mcp.py --file Scripts/OpenMassCrowd/CentralNetwork/project_central_source_with_cesium.py --timeout 120
python Scripts/OpenMassCrowd/CentralNetwork/central_osm_pipeline.py validate-projected Scripts/OpenMassCrowd/CentralNetwork/Data/central_pedestrian_source_unreal.json
```

Projection does not certify ground support and deliberately leaves all cells and spawn districts disabled.

## Review overlay

```powershell
python Scripts/OpenMassCrowd/run_unreal_python_via_mcp.py --file Scripts/OpenMassCrowd/CentralNetwork/review_central_network_overlay.py --timeout 240
```

The overlay uses transient debug drawing for imported paths, crossings, manual-review candidates, manual corrections, rejected candidates, certified lanes, portals and districts. It moves the editor camera to the core but does not spawn actors or save the level. When Central Cesium tiles are not streamed, the report records failed surface snaps instead of claiming that candidate geometry is grounded.

## Audit disconnected topology without changing trust

```powershell
python Scripts/OpenMassCrowd/CentralNetwork/audit_central_topology_evidence.py --self-test
python Scripts/OpenMassCrowd/CentralNetwork/audit_central_topology_evidence.py
```

The audit separates point-ID/projection defects, non-noded geometric intersections, filtered OSM features, and strict Cesium certification cuts. It reports an automatic recovery candidate only when two original OSM lanes independently passed certification, intersect exactly at the same XY, have ordinary ground-level semantics, and differ by no more than 5 cm in certified height. Near endpoints, generated connectors, indoor routes, bridges, tunnels, covered paths, transitions, and cross-layer intersections remain untrusted and require explicit review. The audit is read-only and can never promote a cache.

## Import the certified runtime asset

First verify the certified mirror on host Python:

```powershell
python Scripts/OpenMassCrowd/verify_central_network_cache.py --cache Scripts/OpenMassCrowd/CentralNetwork/Data/central_network_certified.json --source Scripts/OpenMassCrowd/CentralNetwork/Data/central_pedestrian_source.json
python Scripts/OpenMassCrowd/CentralNetwork/test_central_certified_import_common.py
```

After the OpenMassCrowd plugin containing `UOpenMassCrowdCentralNetworkDataAsset` has been rebuilt and Unreal Editor restarted, run:

```powershell
python Scripts/OpenMassCrowd/run_unreal_python_via_mcp.py --file Scripts/OpenMassCrowd/CentralNetwork/import_central_certified_network_asset.py --timeout 240
```

The importer has a fixed input path and validates duplicate keys, the certified schema, the canonical cell/root hash contract, every `certified=true` gate, references, six enabled districts, total population 300 and source provenance before touching `/Game/OpenMassCrowd/Central/DA_CentralNetwork_Certified`. A successful import writes `Data/central_network_asset_import.audit.json`.
