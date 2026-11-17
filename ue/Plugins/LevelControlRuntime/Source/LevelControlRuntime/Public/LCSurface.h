#pragma once
#include "CoreMinimal.h"
#include "Dom/JsonObject.h"

class AActor;
class UInstancedStaticMeshComponent;
class UDecalComponent;
struct FLCEntity;

TSharedPtr<FJsonObject> LCSurfacePresets();
bool LCSurfacePatch(const TSharedPtr<FJsonObject>& Current, const FString& Preset,
    const TSharedPtr<FJsonObject>& Values, TSharedPtr<FJsonObject>& Out, FString& Error);
FString LCBatchKey(const FLCEntity& Entity);
UInstancedStaticMeshComponent* LCCreateBatch(AActor* Host, const FLCEntity& Entity, bool Preview, FString& Error);
UDecalComponent* LCCreateDecal(AActor* Host, const FLCEntity& Entity, FString& Error, bool Preview = false);
