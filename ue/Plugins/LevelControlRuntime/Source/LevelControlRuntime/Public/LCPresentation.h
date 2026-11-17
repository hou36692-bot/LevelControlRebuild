#pragma once
#include "CoreMinimal.h"
#include "UObject/Object.h"
#include "LCTypes.h"
#include "Components/InstancedStaticMeshComponent.h"
#include "LCPresentation.generated.h"

class ULCWireframeComponent;
class ACameraActor;
class UAudioComponent;
class ULCMusicWave;
class UDecalComponent;
class SWidget;

struct FLCFlight
{
    FLCEntity Entity;
    FPrimitiveInstanceId Instance;
    double Start = 0;
    double Duration = 0.8;
    bool Created = false;
    bool Settled = false;
    bool Animate = true;
    FString GrowthKey;
    FString BatchKey;
};

struct FLCGrowthGroup
{
    FString Name;
    FBox Bounds = FBox(ForceInit);
    double Started = -1;
    double Duration = 3;
    double Delay = 0;
    double Height = -100000000;
    double Progress = 0;
    int32 Count = 0;
    int32 Prepared = 0;
    TSet<FString> BatchKeys;
    TArray<FString> DecalIds;
};

struct FLCGrowthRegion
{
    FString Id;
    FString Label;
    FBox Bounds = FBox(ForceInit);
    double Started = 0;
    double Duration = 3;
    double Progress = 0;
};

UCLASS()
class LEVELCONTROLRUNTIME_API ULCPresentation : public UObject
{
    GENERATED_BODY()
public:
    virtual UWorld* GetWorld() const override;
    virtual void BeginDestroy() override;
    bool Initialize(AActor* Owner);
    bool Publish(const TSharedPtr<FJsonObject>& Request, FString& Error);
    void Tick(float DeltaTime);
    void Cancel();
    void ReturnCamera();
    void Committed();
    void Handoff(const FString& EntityId);
    bool Skip(const FString& Reason = TEXT("requested"));
    bool ShouldAutoSkip() const { return AutoSkip && !Instant; }
    bool Matches(const TSharedPtr<FJsonObject>& Request, FString& Error) const;
    bool IsSettled() const { return State == TEXT("settled"); }
    bool IsCancelled() const { return State == TEXT("cancelled"); }
    TSharedPtr<FJsonObject> Status() const;
    TSharedPtr<FJsonObject> Events(int64 Since) const;
    FString OverlayCaption() const;
    bool OverlayFocus(FVector& Position, FString& Text) const;
    FString Source;
    int64 Revision = 0;

private:
    TSharedPtr<FJsonObject> Event(const FString& Type);
    void Shot(const TSharedPtr<FJsonObject>& Data, const FString& Name);
    void ClearGeometry();
    FString PlayerSkipReason() const;
    void UpdateCamera(float DeltaTime);
    void UpdateOverlay();
    void RemoveFlight(const FLCFlight& Flight);
    UPROPERTY() TObjectPtr<AActor> Host;
    UPROPERTY() TObjectPtr<ULCWireframeComponent> Wireframe;
    UPROPERTY() TObjectPtr<ACameraActor> Camera;
    UPROPERTY() TObjectPtr<UAudioComponent> MusicAudio;
    UPROPERTY() TObjectPtr<ULCMusicWave> MusicWave;
    UPROPERTY() TMap<FString, TObjectPtr<UInstancedStaticMeshComponent>> Batches;
    UPROPERTY() TMap<FString, TObjectPtr<UDecalComponent>> Decals;
    TWeakObjectPtr<AActor> PreviousView;
    TArray<FLCFlight> Flights;
    TMap<FString, int32> FlightIndices;
    TArray<FBox> RevealBounds;
    TMap<FString, FLCGrowthGroup> GrowthGroups;
    TArray<FLCGrowthRegion> GrowthRegions;
    TMap<FString, FString> GroupLabels;
    TSharedPtr<SWidget> Overlay;
    FString Caption;
    FVector CameraTargetLocation;
    FRotator CameraTargetRotation;
    FRotator LastControlRotation;
    double LastCameraCut = 0;
    bool Director = false;
    bool CameraReleased = false;
    bool HasCameraTarget = false;
    TSharedPtr<FJsonObject> Cameras;
    TArray<TSharedPtr<FJsonValue>> EventLog;
    FString Session = FGuid::NewGuid().ToString(EGuidFormats::Digits);
    FString State = TEXT("idle");
    FString CameraStage = TEXT("player");
    FString CurrentGroup;
    int64 Sequence = 0;
    int32 Cursor = 0;
    int32 Settled = 0;
    int32 Created = 0;
    int32 RegionCount = 0;
    double Started = 0;
    double FirstVisible = -1;
    double Ended = 0;
    double HeroUntil = 0;
    double MaxTickMs = 0;
    double MaxFrameMs = 0;
    double FrameTotal = 0;
    int32 FrameCount = 0;
    bool OwnsCamera = false;
    bool CameraEnabled = false;
    bool Instant = false;
    bool AutoSkip = false;
    int32 AnimatedCount = 0;
    int32 HandoffCount = 0;
    double FinishDelay = 1;
    FString SkipReason;
    FString Music = TEXT("none");
    double MaxBeatErrorMs = 0;
    double AudioOutputLatencyMs = 0;
};
