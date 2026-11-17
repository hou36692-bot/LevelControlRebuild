using UnrealBuildTool;

public class ProjectLC : ModuleRules
{
    public ProjectLC(ReadOnlyTargetRules Target) : base(Target)
    {
        PCHUsage = PCHUsageMode.UseExplicitOrSharedPCHs;
        PublicDependencyModuleNames.AddRange(new[] {
            "Core", "CoreUObject", "Engine", "InputCore", "EnhancedInput"
        });
        PrivateDependencyModuleNames.AddRange(new[] {
            "HTTPServer", "SQLiteCore", "Json", "DeveloperSettings"
        });
    }
}
