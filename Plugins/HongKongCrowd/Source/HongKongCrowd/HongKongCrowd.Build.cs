using UnrealBuildTool;

public class HongKongCrowd : ModuleRules
{
    public HongKongCrowd(ReadOnlyTargetRules Target) : base(Target)
    {
        PCHUsage = PCHUsageMode.UseExplicitOrSharedPCHs;

        PublicDependencyModuleNames.AddRange(new[]
        {
            "Core",
            "CoreUObject",
            "Engine",
            "AIModule",
            "NavigationSystem"
        });
    }
}
