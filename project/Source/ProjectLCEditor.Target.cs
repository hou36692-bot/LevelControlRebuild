using UnrealBuildTool;

public class ProjectLCEditorTarget : TargetRules
{
    public ProjectLCEditorTarget(TargetInfo Target) : base(Target)
    {
        Type = TargetType.Editor;
        DefaultBuildSettings = BuildSettingsVersion.V7;
        IncludeOrderVersion = EngineIncludeOrderVersion.Unreal5_8;
        ExtraModuleNames.Add("ProjectLC");
    }
}
