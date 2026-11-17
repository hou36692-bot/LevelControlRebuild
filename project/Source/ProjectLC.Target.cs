using UnrealBuildTool;

public class ProjectLCTarget : TargetRules
{
    public ProjectLCTarget(TargetInfo Target) : base(Target)
    {
        Type = TargetType.Game;
        DefaultBuildSettings = BuildSettingsVersion.V7;
        IncludeOrderVersion = EngineIncludeOrderVersion.Unreal5_8;
        ExtraModuleNames.Add("ProjectLC");
    }
}
