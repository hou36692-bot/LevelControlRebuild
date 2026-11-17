#pragma once
#include "CoreMinimal.h"
#include "Engine/DeveloperSettings.h"
#include "LCSettings.generated.h"

UCLASS(Config=Game, DefaultConfig, meta=(DisplayName="Level Control"))
class LEVELCONTROLRUNTIME_API ULCSettings : public UDeveloperSettings
{
    GENERATED_BODY()
public:
    UPROPERTY(Config, EditAnywhere, Category="Connection", meta=(ClampMin="1024", ClampMax="65535"))
    int32 Port = 18844;
    UPROPERTY(Config, EditAnywhere, Category="Audio", meta=(ClampMin="0", ClampMax="300"))
    float AudioOutputLatencyMs = 0;
};
