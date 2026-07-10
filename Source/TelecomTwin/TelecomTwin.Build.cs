using UnrealBuildTool;

public class TelecomTwin : ModuleRules
{
    public TelecomTwin(ReadOnlyTargetRules Target) : base(Target)
    {
        PCHUsage = PCHUsageMode.UseExplicitOrSharedPCHs;

        PublicDependencyModuleNames.AddRange(
            new string[]
            {
                "Core",
                "CoreUObject",
                "Engine",
                "Json",
                "JsonUtilities",
                "Networking",
                "Sockets"
            }
        );
    }
}
