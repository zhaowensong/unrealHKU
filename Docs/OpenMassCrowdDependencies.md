# Open Mass crowd dependency record

## TelecomTwin safety baseline

- Baseline commit: `1ec2471f697a774d4f8cb4513938b4493ed49383`
- Rollback tag: `rollback/pre-open-mass-crowd-2026-07-13`
- Experiment branch: `experiment/open-mass-crowd-ue57`
- Existing signal-ray source and generated scene are out of scope for this experiment.

## Installed UE 5.7 plugins

The following plugin descriptors were found under `D:\astrea\UE_5.7\Engine\Plugins`:

- `AI/MassCrowd/MassCrowd.uplugin`
- `Runtime/MassEntity/MassEntity.uplugin`
- `Runtime/MassGameplay/MassGameplay.uplugin`
- `AI/MassAI/MassAI.uplugin`
- `Runtime/ZoneGraph/ZoneGraph.uplugin`
- `Runtime/StateTree/StateTree.uplugin`

MassCrowd, MassGameplay, MassAI, and ZoneGraph are marked experimental in the installed descriptors. They are acceptable for this bounded demo but must remain isolated and reversible.

## Third-party source

- Candidate: <https://github.com/Ji-Rath/MassAIExample>
- License: MIT (`LICENSE` in the upstream repository)
- Target: upstream UE 5.7 release
- Integration rule: validate upstream separately, then copy/adapt only required source and configuration with attribution.

## Optional visual content

- Epic City Sample Crowds: <https://www.fab.com/listings/903037e9-e1ac-4f41-96e8-1683c6fa7ad4>
- Cost: free
- License boundary: UE-Only Content, usable only with Unreal Engine-based products
- Role: optional characters, clothing, and animation assets; not the crowd simulation implementation.

