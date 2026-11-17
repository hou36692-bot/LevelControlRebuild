using UnrealBuildTool;

public class LevelControlRuntime : ModuleRules
{
    public LevelControlRuntime(ReadOnlyTargetRules Target) : base(Target)
    {
        PCHUsage = PCHUsageMode.UseExplicitOrSharedPCHs;
        PublicDependencyModuleNames.AddRange(new[] { "Core", "CoreUObject", "Engine", "DeveloperSettings", "Json", "HTTPServer", "SQLiteCore" });
        PrivateDependencyModuleNames.AddRange(new[] { "RenderCore", "RHI", "Projects", "AudioExtensions", "ImageWrapper", "ImageCore", "Slate", "SlateCore", "InputCore" });
        RuntimeDependencies.Add("$(EngineDir)/Content/Slate/Fonts/DroidSansFallback.ttf", StagedFileType.UFS);
        RuntimeDependencies.Add("$(PluginDir)/Resources/architect-pulse.wav", StagedFileType.NonUFS);
        RuntimeDependencies.Add("$(PluginDir)/Resources/catalog.json", StagedFileType.NonUFS);
    }
}
