#pragma once
#include "CoreMinimal.h"
#include "UObject/Object.h"
#include "Components/InstancedStaticMeshComponent.h"
#include "LCTypes.h"
#include "LCStore.h"
#include "Async/Future.h"
#include "LCSemantic.h"
#include "LCTxService.generated.h"

class ULCWireframeComponent;
class ULCPresentation;
class ULCEnvironment;
class UDecalComponent;

struct FLCInstance
{
    FString Template;
    FPrimitiveInstanceId Handle;
};

struct FLCFrameSample
{
    uint64 Sequence;
    double Ms;
    int64 Version;
    bool Editing;
    bool Capture;
};

UCLASS()
class LEVELCONTROLRUNTIME_API ULCTxService : public UObject
{
    GENERATED_BODY()
public:
    bool Initialize(UWorld* World);
    void Shutdown();
    void Tick(float DeltaTime = 0);
    TSharedPtr<FJsonObject> Submit(const TSharedPtr<FJsonObject>& Request, bool Undo = false, const FString& Mode = TEXT(""), const FString& TransactionId = TEXT(""));
    TSharedPtr<FJsonObject> Preview(const TSharedPtr<FJsonObject>& Request);
    TSharedPtr<FJsonObject> Control(const TSharedPtr<FJsonObject>& Request);
    TSharedPtr<FJsonObject> Commit(const TSharedPtr<FJsonObject>& Request);
    TSharedPtr<FJsonObject> PresentationStatus() const;
    TSharedPtr<FJsonObject> Events(int64 Since) const;
    TSharedPtr<FJsonObject> Checkpoints();
    TSharedPtr<FJsonObject> Check(const TSharedPtr<FJsonObject>& Request);
    TSharedPtr<FJsonObject> PlayerControl(const TSharedPtr<FJsonObject>& Request);
    TSharedPtr<FJsonObject> ActionControl(const TSharedPtr<FJsonObject>& Request);
    TSharedPtr<FJsonObject> Receipt(const FString& RequestId) const;
    TSharedPtr<FJsonObject> Consistency();
    TSharedPtr<FJsonObject> Select(const TSharedPtr<FJsonObject>& Request);
    TSharedPtr<FJsonObject> Capture(const TSharedPtr<FJsonObject>& Request);
    TSharedPtr<FJsonObject> EnvironmentRead(bool PresetsOnly = false) const;
    TSharedPtr<FJsonObject> EnvironmentControl(const TSharedPtr<FJsonObject>& Request);
    TSharedPtr<FJsonObject> DesignControl(const TSharedPtr<FJsonObject>& Request);
    TSharedPtr<FJsonObject> ReviewControl(const TSharedPtr<FJsonObject>& Request);
    TSharedPtr<FJsonObject> FrameHistoryRead(int64 Since) const;
    bool IsBusy() const { return Phase != EPhase::Idle; }
    int64 GetVersion() const { return Version; }
    const TMap<FString, FLCEntity>& GetEntities() const { return Entities; }
    const TMap<FString, FLCEntity>& GetCommittedEntities() const { return CommittedEntities; }
    TSharedPtr<FJsonObject> SemanticQuery(const TSharedPtr<FJsonObject>& Request);
    TSharedPtr<FJsonObject> SemanticChanges(const TSharedPtr<FJsonObject>& Request);
    TSharedPtr<FJsonObject> SemanticRead(const FString& Source) const;
    int32 GetInstanceCount() const;

private:
    friend class FLCTemplateTest;
    bool Create(const FLCEntity& Entity, FString& Handle, FString& Error);
    bool Remove(const FString& Id);
    bool WriteState(const FString& Id, const TSharedPtr<FJsonObject>& State);
    bool OverlapsPlayer(const FLCEntity& Entity) const;
    TSharedPtr<FJsonObject> MakeReceipt(const FString& RequestId, const FString& Status, const FString& Warning = TEXT("")) const;
    bool Plan(const TSharedPtr<FJsonObject>& Request, bool Undo, FString& Error);
    bool PlanSnapshot(const TSharedPtr<FJsonObject>& Request, const FString& Mode, FString& Error);
    bool PlanAction(const TSharedPtr<FJsonObject>& Request, FString& Error);
    bool PlanEnvironment(const TSharedPtr<FJsonObject>& Request, FString& Error);
    bool PlanAnnotations(const TSharedPtr<FJsonObject>& Request, FString& Error);
    bool PlanSemanticState(const TSharedPtr<FJsonObject>& Request, const FString& Mode, bool Undo, FString& Error);
    bool ApplyInstanceOverride(FLCEntity& Entity, FString& Error) const;
    bool ResolveEditTargets(const FString& Selector, TArray<FString>& Ids, FString& Error);
    FString Snapshot() const;
    bool WriteEnvironment(const TSharedPtr<FJsonObject>& State, FString& Error);
    void CancelDesign();
    void TickMeasurement(float DeltaTime);
    TSharedPtr<FJsonObject> CompareCaptures(const TSharedPtr<FJsonObject>& Request);
    void ReturnReviewCamera();
    void BeginScreenshot();
    void ScreenshotPixels(int32 Width, int32 Height, const TArray<FColor>& Pixels);
    void TickAction();
    void CancelQueued();
    void Finish(const FString& Status, const FString& Warning = TEXT(""));
    void RefreshSelection();
    FString HitId(const FHitResult& Hit) const;
    enum class EPhase { Idle, Apply, Persist, Finalize, Rollback, Capture, PreAction, Animate, Measure };
    EPhase Phase = EPhase::Idle;
    FLCStoreEdit Pending;
    TFuture<bool> StoreJob;
    int32 Cursor = 0;
    double Started = 0;
    double FirstVisible = 0;
    double MaxFrameMs = 0;
    FString Failure;
    FString CapturePath;
    bool CaptureIncludeUI = false;
    FString RuntimeDataDirectory;
    TFuture<bool> CaptureJob;
    FDelegateHandle ScreenshotHandle;
    TArray<TSharedPtr<FJsonValue>> CaptureViews;
    TArray<TSharedPtr<FJsonValue>> CapturedViews;
    int32 CaptureIndex = 0;
    int32 CaptureDelayFrames = 0;
    UPROPERTY() TObjectPtr<class ACameraActor> ReviewCamera;
    TWeakObjectPtr<AActor> ReviewPreviousView;
    TArray<double> MeasurementFrames;
    TArray<int32> MeasurementDrawCalls;
    int32 MeasurementTarget = 0;
    TArray<FLCFrameSample> FrameHistory;
    uint64 FrameSequence = 0;
    UPROPERTY() TObjectPtr<AActor> Host;
    UPROPERTY() TObjectPtr<ULCWireframeComponent> Indicator;
    UPROPERTY() TObjectPtr<ULCPresentation> Presentation;
    UPROPERTY() TObjectPtr<ULCEnvironment> EnvironmentRenderer;
    TSharedPtr<FJsonObject> Environment;
    TSharedPtr<FJsonObject> EnvironmentPreview;
    int64 EnvironmentPreviewVersion = -1;
    double EnvironmentRate = 0;
    TSharedPtr<FJsonObject> DesignPreview;
    TArray<FString> DesignHiddenIds;
    int64 DesignPreviewVersion = -1;
    TSharedPtr<FJsonObject> QueuedCommit;
    FString PendingMode;
    double CommitHeldUntil = 0;
    TMap<FString, double> HoldLeases;
    double HoldDeadline() const;
    TArray<FString> SelectedIds;
    int64 SelectionVersion = -1;
    UPROPERTY() TMap<FString, TObjectPtr<UInstancedStaticMeshComponent>> Batches;
    UPROPERTY() TMap<FString, TObjectPtr<UDecalComponent>> Decals;
    TMap<FString, FLCInstance> Instances;
    TMap<FString, FLCEntity> Entities;
    TMap<FString, FLCEntity> CommittedEntities;
    TSharedPtr<FJsonObject> SemanticState;
    TSharedPtr<FJsonObject> CommittedSemanticState;
    FLCSemanticWorld SemanticWorld;
    TArray<FString> LockedSelection;
    FLCStore Store;
    TMap<FString, TSharedPtr<FJsonObject>> Receipts;
    int64 Version = 0;
    TSharedPtr<FJsonObject> ActionRequest;
    TArray<FLCEntity> ActionFrom;
    TArray<FLCEntity> ActionTo;
    double ActionStarted = 0;
    double ActionDuration = 0;
    double ActionPausedAt = 0;
    bool ActionStopRequested = false;
    int32 ActionCursor = 0;
};
