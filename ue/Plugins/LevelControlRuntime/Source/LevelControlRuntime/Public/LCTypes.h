#pragma once
#include "CoreMinimal.h"
#include "Dom/JsonObject.h"

struct LEVELCONTROLRUNTIME_API FLCTemplate
{
    FString Id;
    FString Mesh;
    FVector Size;
    FVector MeshSize = FVector(100);
    FVector MeshCenter = FVector::ZeroVector;
};

struct LEVELCONTROLRUNTIME_API FLCEntity
{
    FString Id;
    FString Template;
    FVector Position = FVector::ZeroVector;
    FVector Rotation = FVector::ZeroVector;
    FVector Size = FVector(100);
    TArray<FString> Tags;
    FString Group;
    FString Source;
    int64 Version = 0;
    TSharedPtr<FJsonObject> Surface;
    bool Collision = true;
    int32 Lod = 0;
    TSharedPtr<FJsonObject> Semantic;

    FTransform Transform() const;
    FBox Bounds() const;
    TSharedPtr<FJsonObject> ToJson() const;
    static bool FromJson(const TSharedPtr<FJsonObject>& Json, FLCEntity& Entity, FString& Error);
};

LEVELCONTROLRUNTIME_API const TArray<FLCTemplate>& LCTemplates();
LEVELCONTROLRUNTIME_API TSharedPtr<FJsonObject> LCAssetCatalog();
LEVELCONTROLRUNTIME_API bool LCValidId(const FString& Id);
LEVELCONTROLRUNTIME_API TArray<TSharedPtr<FJsonValue>> LCVector(const FVector& Value);
LEVELCONTROLRUNTIME_API bool LCReadVector(const TSharedPtr<FJsonObject>& Json, const FString& Key, FVector& Out);
LEVELCONTROLRUNTIME_API FString LCJsonString(const TSharedPtr<FJsonObject>& Json);
LEVELCONTROLRUNTIME_API TSharedPtr<FJsonObject> LCParseJson(const FString& Text);
