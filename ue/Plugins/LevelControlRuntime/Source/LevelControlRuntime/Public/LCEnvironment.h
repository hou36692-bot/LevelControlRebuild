#pragma once
#include "CoreMinimal.h"
#include "Dom/JsonObject.h"
#include "LCEnvironment.generated.h"

class UDirectionalLightComponent;
class USkyLightComponent;
class USkyAtmosphereComponent;
class UExponentialHeightFogComponent;
class UVolumetricCloudComponent;
class UPostProcessComponent;
class UMaterialInstanceDynamic;

UCLASS()
class ULCEnvironment : public UObject
{
    GENERATED_BODY()
public:
    bool Initialize(AActor* Host);
    bool Apply(const TSharedPtr<FJsonObject>& Values, FString& Error);
    TSharedPtr<FJsonObject> Observe() const;
    static TSharedPtr<FJsonObject> Defaults();
    static TSharedPtr<FJsonObject> Presets();
    static bool Patch(const TSharedPtr<FJsonObject>& Base, const TSharedPtr<FJsonObject>& Patch,
        TSharedPtr<FJsonObject>& Result, FString& Error);
    TSharedPtr<FJsonObject> Initial;
private:
    UPROPERTY() TObjectPtr<UDirectionalLightComponent> Sun;
    UPROPERTY() TObjectPtr<USkyLightComponent> Sky;
    UPROPERTY() TObjectPtr<USkyAtmosphereComponent> Atmosphere;
    UPROPERTY() TObjectPtr<UExponentialHeightFogComponent> Fog;
    UPROPERTY() TObjectPtr<UVolumetricCloudComponent> Cloud;
    UPROPERTY() TObjectPtr<UPostProcessComponent> Post;
    UPROPERTY() TObjectPtr<UMaterialInstanceDynamic> CloudMaterial;
    FName CloudCoverageParameter;
    float AtmosphereScale = 0.0331f;
};
