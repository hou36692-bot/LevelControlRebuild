#include "LCEnvironment.h"
#include "LCTypes.h"
#include "Components/DirectionalLightComponent.h"
#include "Components/SkyLightComponent.h"
#include "Components/SkyAtmosphereComponent.h"
#include "Components/ExponentialHeightFogComponent.h"
#include "Components/VolumetricCloudComponent.h"
#include "Components/PostProcessComponent.h"
#include "Materials/MaterialInstanceDynamic.h"
#include "EngineUtils.h"
#include "Engine/World.h"
#include "GameFramework/Actor.h"

namespace
{
struct FParameter { const TCHAR* Name; double Initial; double Min; double Max; };
const FParameter EnvironmentParameters[] = {
    {TEXT("time_h"), 12, 0, 24}, {TEXT("sun_azimuth_deg"), 325, 0, 360},
    {TEXT("sun_elevation_deg"), 45, -90, 90}, {TEXT("sun_intensity"), 3, 0, 150000},
    {TEXT("sun_temperature_k"), 6500, 1000, 12000}, {TEXT("sky_intensity"), 0.7, 0, 10},
    {TEXT("atmosphere_density"), 1, 0, 4}, {TEXT("fog_density"), 0, 0, 0.1},
    {TEXT("fog_height_cm"), 0, -100000, 100000}, {TEXT("fog_falloff"), 0.2, 0.001, 2},
    {TEXT("cloud_coverage"), 0, 0, 1}, {TEXT("exposure_ev"), 0, -10, 10},
    {TEXT("contrast"), 1, 0, 2}, {TEXT("saturation"), 1, 0, 2},
    {TEXT("temperature_k"), 6500, 1500, 15000}
};

template<class T> T* FindOrCreate(AActor* Host)
{
    for (TActorIterator<AActor> It(Host->GetWorld()); It; ++It)
        if (auto* Component = It->FindComponentByClass<T>()) return Component;
    auto* Component = NewObject<T>(Host);
    Component->SetupAttachment(Host->GetRootComponent());
    Host->AddInstanceComponent(Component);
    Component->RegisterComponent();
    return Component;
}
}

TSharedPtr<FJsonObject> ULCEnvironment::Defaults()
{
    auto Values = MakeShared<FJsonObject>();
    for (const auto& Parameter : EnvironmentParameters) Values->SetNumberField(Parameter.Name, Parameter.Initial);
    Values->SetArrayField(TEXT("fog_color"), LCVector(FVector(0.55, 0.65, 0.8)));
    return Values;
}

bool ULCEnvironment::Patch(const TSharedPtr<FJsonObject>& Base, const TSharedPtr<FJsonObject>& Changes,
    TSharedPtr<FJsonObject>& Result, FString& Error)
{
    if (!Base || !Changes) { Error = TEXT("Environment values must be an object"); return false; }
    Result = LCParseJson(LCJsonString(Base));
    for (const auto& Field : Changes->Values)
    {
        const FString Key(Field.Key);
        if (Key == TEXT("fog_color"))
        {
            FVector Color;
            if (!LCReadVector(Changes, Key, Color) || Color.GetMin() < 0 || Color.GetMax() > 1)
            { Error = TEXT("fog_color must contain three numbers within 0..1"); return false; }
        }
        else
        {
            const FParameter* Found = nullptr;
            for (const auto& Parameter : EnvironmentParameters) if (Key == Parameter.Name) { Found = &Parameter; break; }
            double Value = 0;
            if (!Found) { Error = TEXT("Unknown environment parameter: ") + Key; return false; }
            if (Field.Value->Type != EJson::Number || !Field.Value->TryGetNumber(Value) || !FMath::IsFinite(Value) || Value < Found->Min || Value > Found->Max)
            { Error = FString::Printf(TEXT("%s must be within %.3f..%.3f"), *Key, Found->Min, Found->Max); return false; }
        }
        Result->SetField(Key, Field.Value);
    }
    if (Changes->HasField(TEXT("time_h")))
    {
        const double Time = Changes->GetNumberField(TEXT("time_h"));
        if (!Changes->HasField(TEXT("sun_elevation_deg"))) Result->SetNumberField(TEXT("sun_elevation_deg"), 75 * FMath::Sin((Time - 6) * PI / 12));
        if (!Changes->HasField(TEXT("sun_azimuth_deg"))) Result->SetNumberField(TEXT("sun_azimuth_deg"), FMath::Fmod(Time * 15 + 90, 360));
    }
    return true;
}

TSharedPtr<FJsonObject> ULCEnvironment::Presets()
{
    auto Result = MakeShared<FJsonObject>();
    const TCHAR* Names[] = {TEXT("morning"), TEXT("noon"), TEXT("golden-hour"), TEXT("dusk"), TEXT("night"), TEXT("overcast")};
    const double Times[] = {7, 12, 17.5, 18.7, 23, 12};
    const double Intensities[] = {3, 6, 4, 0.7, 0.08, 1.2};
    const double Temperatures[] = {4800, 6500, 3200, 8000, 10000, 7000};
    for (int32 Index = 0; Index < 6; ++Index)
    {
        auto Changes = MakeShared<FJsonObject>();
        Changes->SetNumberField(TEXT("time_h"), Times[Index]);
        Changes->SetNumberField(TEXT("sun_intensity"), Intensities[Index]);
        Changes->SetNumberField(TEXT("sun_temperature_k"), Temperatures[Index]);
        Changes->SetNumberField(TEXT("sky_intensity"), Index == 4 ? 0.12 : Index == 3 ? 0.3 : 0.7);
        Changes->SetNumberField(TEXT("fog_density"), Index == 5 ? 0.025 : Index == 4 ? 0.006 : 0.008);
        Changes->SetNumberField(TEXT("cloud_coverage"), Index == 5 ? 0.9 : Index == 4 ? 0.05 : 0.3);
        Changes->SetNumberField(TEXT("saturation"), Index == 5 ? 0.75 : 1.0);
        if (Index == 4) Changes->SetNumberField(TEXT("sun_elevation_deg"), 25);
        TSharedPtr<FJsonObject> Values; FString Error;
        Patch(Defaults(), Changes, Values, Error);
        Result->SetObjectField(Names[Index], Values);
    }
    return Result;
}

bool ULCEnvironment::Initialize(AActor* Host)
{
    Sun = FindOrCreate<UDirectionalLightComponent>(Host);
    Sky = FindOrCreate<USkyLightComponent>(Host);
    Atmosphere = FindOrCreate<USkyAtmosphereComponent>(Host);
    AtmosphereScale = FMath::Max(Atmosphere->RayleighScatteringScale, 0.000001f);
    Fog = FindOrCreate<UExponentialHeightFogComponent>(Host);
    Cloud = FindOrCreate<UVolumetricCloudComponent>(Host);
    Post = NewObject<UPostProcessComponent>(Host);
    Post->bUnbound = true;
    Post->Priority = 100;
    Post->SetupAttachment(Host->GetRootComponent());
    Host->AddInstanceComponent(Post); Post->RegisterComponent();
    Sun->SetMobility(EComponentMobility::Movable);
    Sky->SetMobility(EComponentMobility::Movable);
    Initial = Defaults();
    Initial->SetNumberField(TEXT("sun_intensity"), Sun->Intensity);
    Initial->SetNumberField(TEXT("sun_temperature_k"), Sun->bUseTemperature ? Sun->Temperature : 6500);
    Initial->SetNumberField(TEXT("sun_azimuth_deg"), FRotator::ClampAxis(Sun->GetComponentRotation().Yaw));
    Initial->SetNumberField(TEXT("sun_elevation_deg"), -Sun->GetComponentRotation().Pitch);
    Initial->SetNumberField(TEXT("sky_intensity"), Sky->Intensity);
    // New components start invisible until a committed or preview state activates them.
    Fog->SetFogDensity(0);
    Cloud->SetVisibility(false);
    auto* Material = LoadObject<UMaterialInterface>(nullptr, TEXT("/Engine/EngineSky/VolumetricClouds/m_SimpleVolumetricCloud_Inst.m_SimpleVolumetricCloud_Inst"));
    if (Material)
    {
        CloudMaterial = UMaterialInstanceDynamic::Create(Material, this);
        TArray<FMaterialParameterInfo> Infos; TArray<FGuid> Guids;
        Material->GetAllScalarParameterInfo(Infos, Guids);
        for (const auto& Info : Infos)
            if (Info.Name.ToString().Contains(TEXT("coverage"), ESearchCase::IgnoreCase)) { CloudCoverageParameter = Info.Name; break; }
        Cloud->SetMaterial(CloudMaterial);
        Cloud->SetViewSampleCountScale(0.5);
    }
    return Sun && Sky && Atmosphere && Fog && Cloud && Post;
}

bool ULCEnvironment::Apply(const TSharedPtr<FJsonObject>& Values, FString& Error)
{
    TSharedPtr<FJsonObject> Checked;
    if (!Patch(Defaults(), Values, Checked, Error)) return false;
    if (Values->GetNumberField(TEXT("cloud_coverage")) > 0 && (!CloudMaterial || CloudCoverageParameter.IsNone()))
    { Error = TEXT("Cooked cloud material or its coverage parameter is unavailable"); return false; }
    auto Number = [&](const TCHAR* Name) { return Values->GetNumberField(Name); };
    Sun->SetWorldRotation(FRotator(-Number(TEXT("sun_elevation_deg")), Number(TEXT("sun_azimuth_deg")), 0));
    Sun->SetIntensity(Number(TEXT("sun_intensity")));
    Sun->SetUseTemperature(true); Sun->SetTemperature(Number(TEXT("sun_temperature_k")));
    Sky->SetIntensity(Number(TEXT("sky_intensity")));
    Sky->SetRealTimeCapture(true);
    Atmosphere->SetRayleighScatteringScale(Number(TEXT("atmosphere_density")) * AtmosphereScale);
    Fog->SetFogDensity(Number(TEXT("fog_density")));
    Fog->SetWorldLocation(FVector(0, 0, Number(TEXT("fog_height_cm"))));
    Fog->SetFogHeightFalloff(Number(TEXT("fog_falloff")));
    FVector Color; LCReadVector(Values, TEXT("fog_color"), Color);
    Fog->SetFogInscatteringColor(FLinearColor(Color));
    Cloud->SetVisibility(Number(TEXT("cloud_coverage")) > 0);
    if (CloudMaterial && !CloudCoverageParameter.IsNone()) CloudMaterial->SetScalarParameterValue(CloudCoverageParameter, Number(TEXT("cloud_coverage")) * 2 - 1);
    auto& Settings = Post->Settings;
    Settings.bOverride_AutoExposureBias = true; Settings.AutoExposureBias = Number(TEXT("exposure_ev"));
    Settings.bOverride_ColorContrast = true; Settings.ColorContrast = FVector4(1, 1, 1, Number(TEXT("contrast")));
    Settings.bOverride_ColorSaturation = true; Settings.ColorSaturation = FVector4(1, 1, 1, Number(TEXT("saturation")));
    Settings.bOverride_WhiteTemp = true; Settings.WhiteTemp = Number(TEXT("temperature_k"));
    return true;
}

TSharedPtr<FJsonObject> ULCEnvironment::Observe() const
{
    auto Result = MakeShared<FJsonObject>();
    Result->SetNumberField(TEXT("sun_azimuth_deg"), FRotator::ClampAxis(Sun->GetComponentRotation().Yaw));
    Result->SetNumberField(TEXT("sun_elevation_deg"), -Sun->GetComponentRotation().Pitch);
    Result->SetNumberField(TEXT("sun_intensity"), Sun->Intensity);
    Result->SetNumberField(TEXT("sun_temperature_k"), Sun->Temperature);
    Result->SetNumberField(TEXT("sky_intensity"), Sky->Intensity);
    Result->SetNumberField(TEXT("atmosphere_density"), Atmosphere->RayleighScatteringScale / AtmosphereScale);
    Result->SetNumberField(TEXT("fog_density"), Fog->FogDensity);
    Result->SetNumberField(TEXT("fog_height_cm"), Fog->GetComponentLocation().Z);
    Result->SetNumberField(TEXT("fog_falloff"), Fog->FogHeightFalloff);
    const auto Color = Fog->FogInscatteringLuminance;
    Result->SetArrayField(TEXT("fog_color"), LCVector(FVector(Color.R, Color.G, Color.B)));
    Result->SetBoolField(TEXT("cloud_visible"), Cloud->IsVisible());
    Result->SetBoolField(TEXT("cloud_material_ready"), CloudMaterial && !CloudCoverageParameter.IsNone());
    Result->SetStringField(TEXT("cloud_parameter"), CloudCoverageParameter.ToString());
    if (CloudMaterial && !CloudCoverageParameter.IsNone()) Result->SetNumberField(TEXT("cloud_coverage"), (CloudMaterial->K2_GetScalarParameterValue(CloudCoverageParameter) + 1) / 2);
    Result->SetNumberField(TEXT("exposure_ev"), Post->Settings.AutoExposureBias);
    Result->SetNumberField(TEXT("contrast"), Post->Settings.ColorContrast.W);
    Result->SetNumberField(TEXT("saturation"), Post->Settings.ColorSaturation.W);
    Result->SetNumberField(TEXT("temperature_k"), Post->Settings.WhiteTemp);
    return Result;
}
