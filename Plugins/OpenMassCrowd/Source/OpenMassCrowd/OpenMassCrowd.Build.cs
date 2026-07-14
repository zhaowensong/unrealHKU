using UnrealBuildTool;

public class OpenMassCrowd : ModuleRules
{
    public OpenMassCrowd(ReadOnlyTargetRules Target) : base(Target)
    {
        PCHUsage = PCHUsageMode.UseExplicitOrSharedPCHs;

        PublicDependencyModuleNames.AddRange(new[]
        {
            "Core",
            "CoreUObject",
            "Engine",
            "AnimToTexture",
            "MassEntity",
            "MassCommon",
            "MassSpawner",
            "MassSimulation",
            "MassMovement",
            "MassNavigation",
            "MassZoneGraphNavigation",
            "MassCrowd",
            "MassRepresentation",
            "MassLOD",
            "MassActors",
            "ZoneGraph"
        });
    }
}
