#include "LCTxService.h"
#include "LCEnvironment.h"
#include "LCSurface.h"
#include "Camera/CameraActor.h"
#include "Components/DecalComponent.h"
#include "Components/SceneComponent.h"
#include "Engine/StaticMesh.h"
#include "Engine/World.h"
#include "GameFramework/Actor.h"
#include "LCLog.h"
#include "Misc/CommandLine.h"
#include "Misc/Parse.h"
#include "Misc/Paths.h"
#include "Async/Async.h"
#include "Components/CapsuleComponent.h"
#include "GameFramework/Character.h"
#include "Kismet/GameplayStatics.h"
#include "LCSelector.h"
#include "LCWireframeComponent.h"
#include "Engine/GameViewportClient.h"
#include "HAL/FileManager.h"
#include "LCPresentation.h"

bool ULCTxService::Initialize(UWorld* World)
{
    Host = World->SpawnActor<AActor>();
    if (!Host) return false;
    Host->SetFlags(RF_Transient);
    Host->Tags.Add(TEXT("LCInternal"));
    auto* Root = NewObject<USceneComponent>(Host);
    Host->SetRootComponent(Root);
    Root->RegisterComponent();
    Indicator = NewObject<ULCWireframeComponent>(Host);
    Indicator->SetupAttachment(Root);
    Host->AddInstanceComponent(Indicator);
    Indicator->RegisterComponent();
    Presentation = NewObject<ULCPresentation>(this);
    if (!Presentation->Initialize(Host)) { Shutdown(); return false; }
    EnvironmentRenderer = NewObject<ULCEnvironment>(this);
    if (!EnvironmentRenderer->Initialize(Host)) { Shutdown(); return false; }
    Environment = MakeShared<FJsonObject>();
    Environment->SetObjectField(TEXT("values"), EnvironmentRenderer->Initial);
    Environment->SetObjectField(TEXT("presets"), MakeShared<FJsonObject>());
    for (const auto& Template : LCTemplates())
    {
        auto* Batch = NewObject<UInstancedStaticMeshComponent>(Host);
        auto* Mesh = LoadObject<UStaticMesh>(nullptr, *Template.Mesh);
        if (!Mesh) { Shutdown(); return false; }
        Batch->SetStaticMesh(Mesh);
        Batch->SetMobility(EComponentMobility::Movable);
        Batch->SetCollisionProfileName(TEXT("BlockAll"));
        Batch->SetRemoveSwap();
        Batch->SetupAttachment(Root);
        Host->AddInstanceComponent(Batch);
        Batch->RegisterComponent();
        Batches.Add(Template.Id, Batch);
    }
    FString StorePath = FPaths::ConvertRelativePathToFull(FPaths::ProjectSavedDir() / TEXT("LevelControl/entities.sqlite3"));
    FParse::Value(FCommandLine::Get(), TEXT("LCStore="), StorePath);
    RuntimeDataDirectory = FPaths::GetPath(FPaths::ConvertRelativePathToFull(StorePath)) / TEXT("semantic-v3");
    TMap<FString, FLCEntity> Loaded;
    TSharedPtr<FJsonObject> StoredEnvironment;
    if (!Store.Open(StorePath) || !Store.Load(Loaded, Receipts, Version, &StoredEnvironment, &SemanticState))
    {
        UE_LOG(LogLC, Error, TEXT("Store initialization failed: %s"), *Store.Error);
        Shutdown();
        return false;
    }
    if (StoredEnvironment)
    {
        FString Error;
        if (!WriteEnvironment(StoredEnvironment, Error)) { UE_LOG(LogLC, Error, TEXT("%s"), *Error); Shutdown(); return false; }
    }
    for (const auto& Item : Loaded)
    {
        FString Handle, Error;
        if (!Create(Item.Value, Handle, Error)) { Shutdown(); return false; }
    }
    CommittedEntities = Entities;
    CommittedSemanticState = LCParseJson(LCJsonString(SemanticState));
    SemanticWorld.Publish(CommittedEntities, CommittedSemanticState, Version);
    SemanticWorld.Initialize(World, Host);
    SemanticWorld.Session = Presentation->Events(0)->GetStringField(TEXT("session_id"));
    SemanticWorld.Publish(CommittedEntities, CommittedSemanticState, Version);
    return true;
}

void ULCTxService::Shutdown()
{
    SemanticWorld.Shutdown();
    if (CaptureJob.IsValid()) CaptureJob.Wait();
    ReturnReviewCamera();
    if (StoreJob.IsValid()) StoreJob.Wait();
    if (Presentation) Presentation->ReturnCamera();
    Store.Close();
    if (Host) Host->Destroy();
    Host = nullptr;
    Batches.Reset();
    Decals.Reset();
    Instances.Reset();
    Entities.Reset();
}

bool ULCTxService::Create(const FLCEntity& Input, FString& Handle, FString& Error)
{
    FLCEntity Entity;
    if (!FLCEntity::FromJson(Input.ToJson(), Entity, Error)) return false;
    if (Entities.Contains(Entity.Id)) { Error = TEXT("Entity id already exists"); return false; }
    if (Entity.Template.StartsWith(TEXT("decal/")))
    {
        auto* Decal = LCCreateDecal(Host, Entity, Error);
        if (!Decal) return false;
        Decals.Add(Entity.Id, Decal); Entities.Add(Entity.Id, Entity); Handle = Entity.Id;
        return true;
    }
    const FString Key = LCBatchKey(Entity);
    if (!Batches.Contains(Key))
    {
        auto* Visual = LCCreateBatch(Host, Entity, false, Error);
        if (!Visual) return false;
        Batches.Add(Key, Visual);
    }
    auto* Batch = Batches.Find(Key);
    const auto Instance = (*Batch)->AddInstanceById(Entity.Transform(), true);
    Instances.Add(Entity.Id, {Key, Instance});
    Entities.Add(Entity.Id, Entity);
    Handle = Entity.Id;
    return true;
}

bool ULCTxService::Remove(const FString& Id)
{
    if (auto* Decal = Decals.Find(Id))
    { (*Decal)->DestroyComponent(); Decals.Remove(Id); Entities.Remove(Id); return true; }
    const auto* Instance = Instances.Find(Id);
    if (!Instance) return false;
    Batches[Instance->Template]->RemoveInstanceById(Instance->Handle);
    Instances.Remove(Id);
    Entities.Remove(Id);
    return true;
}

int32 ULCTxService::GetInstanceCount() const
{
    int32 Count = Decals.Num();
    for (const auto& Batch : Batches) Count += Batch.Value->GetInstanceCount();
    return Count;
}

TSharedPtr<FJsonObject> ULCTxService::MakeReceipt(const FString& RequestId, const FString& Status, const FString& Warning) const
{
    auto Json = MakeShared<FJsonObject>();
    Json->SetStringField(TEXT("req_id"), RequestId);
    Json->SetStringField(TEXT("tx_id"), TEXT(""));
    Json->SetStringField(TEXT("status"), Status);
    Json->SetNumberField(TEXT("applied"), 0);
    Json->SetArrayField(TEXT("entity_ids"), {});
    Json->SetNumberField(TEXT("version"), Version);
    TArray<TSharedPtr<FJsonValue>> Warnings;
    if (!Warning.IsEmpty()) Warnings.Add(MakeShared<FJsonValueString>(Warning));
    Json->SetArrayField(TEXT("warnings"), Warnings);
    Json->SetNumberField(TEXT("ms"), 0);
    return Json;
}

TSharedPtr<FJsonObject> ULCTxService::Receipt(const FString& RequestId) const
{
    const auto* Found = Receipts.Find(RequestId);
    return Found ? *Found : MakeReceipt(RequestId, TEXT("rejected"), TEXT("Unknown req_id"));
}

bool ULCTxService::OverlapsPlayer(const FLCEntity& Entity) const
{
    if (!Entity.Collision || Entity.Template.StartsWith(TEXT("decal/"))) return false;
    const auto* Player = UGameplayStatics::GetPlayerCharacter(Host->GetWorld(), 0);
    if (!Player) return false;
    const FVector Center = Player->GetActorLocation();
    const auto* Capsule = Player->GetCapsuleComponent();
    const double Radius = Capsule->GetScaledCapsuleRadius();
    const double HalfSegment = Capsule->GetScaledCapsuleHalfHeight() - Radius;
    const FBox Box = Entity.Bounds();
    const double X = FMath::Max3(Box.Min.X - Center.X, Center.X - Box.Max.X, 0.0);
    const double Y = FMath::Max3(Box.Min.Y - Center.Y, Center.Y - Box.Max.Y, 0.0);
    const double Z = FMath::Max3(Box.Min.Z - (Center.Z + HalfSegment), Center.Z - HalfSegment - Box.Max.Z, 0.0);
    return X * X + Y * Y + Z * Z < Radius * Radius;
}

bool ULCTxService::Plan(const TSharedPtr<FJsonObject>& Request, bool Undo, FString& Error)
{
    if (Undo)
    {
        FLCStoreEdit Last;
        FString Target;
        bool Latest = false;
        Request->TryGetBoolField(TEXT("last"), Latest);
        Request->TryGetStringField(TEXT("tx_id"), Target);
        if ((!Latest && Target.IsEmpty()) || !Store.LatestEdit(Last)) { Error = TEXT("No edit to undo"); return false; }
        if (!Target.IsEmpty() && Target != Last.TransactionId) { Error = TEXT("Only the latest active edit can be undone"); return false; }
        Pending.Kind = TEXT("undo");
        Pending.Command = TEXT("undo");
        Pending.UndoTarget = Last.TransactionId;
        for (const auto& Change : Last.Changes)
        {
            if (Change.Id == TEXT("@semantics"))
            { Pending.Changes.Add({Change.Id, SemanticState, Change.Before}); continue; }
            if (Change.Id == TEXT("@environment"))
            { Pending.Changes.Add({Change.Id, Environment, Change.Before}); continue; }
            const auto* Current = Entities.Find(Change.Id);
            TSharedPtr<FJsonObject> Restored;
            if (Change.Before)
            {
                FLCEntity Entity;
                if (!FLCEntity::FromJson(Change.Before, Entity, Error)) return false;
                Entity.Version = Pending.Version;
                if (OverlapsPlayer(Entity)) { Error = TEXT("Undo would overlap player capsule"); return false; }
                Restored = Entity.ToJson();
            }
            Pending.Changes.Add({Change.Id, Current ? Current->ToJson() : nullptr, Restored});
        }
        return true;
    }
    const TArray<TSharedPtr<FJsonValue>>* Operations = nullptr;
    if (!Request->TryGetArrayField(TEXT("operations"), Operations) || Operations->IsEmpty() || Operations->Num() > 16384)
    { Error = TEXT("operations must contain 1..16384 entries"); return false; }
    if (!Request->TryGetStringField(TEXT("command"), Pending.Command)) { Error = TEXT("command is required"); return false; }
    TSet<FString> Touched;
    int32 Deletes = 0;
    for (const auto& Value : *Operations)
    {
        if (Value->Type != EJson::Object) { Error = TEXT("Operation must be an object"); return false; }
        const auto Operation = Value->AsObject();
        FString Verb;
        if (!Operation->TryGetStringField(TEXT("op"), Verb)) { Error = TEXT("Missing operation verb"); return false; }
        TArray<FString> Ids;
        FLCEntity Created;
        if (Verb == TEXT("create"))
        {
            if (!LockedSelection.IsEmpty()) { Error = TEXT("A selection-bound edit cannot create other objects"); return false; }
            const TSharedPtr<FJsonObject>* Data = nullptr;
            if (!Operation->TryGetObjectField(TEXT("entity"), Data) || !FLCEntity::FromJson(*Data, Created, Error)) return false;
            if (Entities.Contains(Created.Id) || SemanticWorld.Rows.Contains(Created.Id)) { Error = TEXT("Entity id already exists: ") + Created.Id; return false; }
            Ids.Add(Created.Id);
        }
        else if (Verb == TEXT("set") || Verb == TEXT("move") || Verb == TEXT("delete") || Verb == TEXT("surface"))
        {
            FString Selector;
            if (!Operation->TryGetStringField(TEXT("selector"), Selector) || !ResolveEditTargets(Selector, Ids, Error)) return false;
            if (Verb == TEXT("set") && ((Selector == TEXT("selected") && Ids.Num() > 1) || (SemanticWorld.Rows.Contains(Selector) && !Entities.Contains(Selector))))
            { Error = TEXT("Absolute set requires one physical leaf; use move or surface for assemblies"); return false; }
        }
        else { Error = TEXT("Unsupported operation: ") + Verb; return false; }
        for (const auto& Id : Ids)
        {
            if (Touched.Contains(Id)) { Error = TEXT("Each entity may appear only once in a transaction"); return false; }
            Touched.Add(Id);
            if (Touched.Num() > 16384) { Error = TEXT("batch_limit is 16384 entities"); return false; }
            const FLCEntity* Old = Entities.Find(Id);
            FLCEntity Entity = Old ? *Old : Created;
            TSharedPtr<FJsonObject> After;
            if (Verb == TEXT("delete")) ++Deletes;
            else
            {
                if (Verb == TEXT("move"))
                {
                    FVector Delta;
                    if (!LCReadVector(Operation, TEXT("delta_cm"), Delta)) { Error = TEXT("Invalid delta_cm"); return false; }
                    Entity.Position += Delta;
                }
                if (Verb == TEXT("surface"))
                {
                    FString Preset;
                    const TSharedPtr<FJsonObject>* Values = nullptr;
                    if (Operation->HasField(TEXT("preset")) && !Operation->TryGetStringField(TEXT("preset"), Preset))
                    { Error = TEXT("preset must be a string"); return false; }
                    if (Operation->HasField(TEXT("values")) && !Operation->TryGetObjectField(TEXT("values"), Values))
                    { Error = TEXT("surface values must be an object"); return false; }
                    if (Preset.IsEmpty() && (!Values || (*Values)->Values.IsEmpty())) { Error = TEXT("Specify a surface preset or values"); return false; }
                    if (!LCSurfacePatch(Entity.Surface, Preset, Values ? *Values : nullptr, Entity.Surface, Error)) return false;
                }
                if (Verb == TEXT("set"))
                {
                    const TSharedPtr<FJsonObject>* Geometry = nullptr;
                    const bool HasTransform = Operation->TryGetObjectField(TEXT("transform"), Geometry);
                    if (Operation->HasField(TEXT("transform")) && !HasTransform) { Error = TEXT("transform must be an object"); return false; }
                    FString Template;
                    if (Operation->HasField(TEXT("template")))
                    {
                        if (!Operation->TryGetStringField(TEXT("template"), Template)) { Error = TEXT("template must be a string"); return false; }
                        Entity.Template = Template;
                    }
                    if (Operation->HasField(TEXT("collision")) && !Operation->TryGetBoolField(TEXT("collision"), Entity.Collision)) { Error = TEXT("collision must be boolean"); return false; }
                    if (Operation->HasField(TEXT("lod")))
                    {
                        double Lod;
                        if (!Operation->TryGetNumberField(TEXT("lod"), Lod) || !FMath::IsFinite(Lod) || Lod != FMath::FloorToDouble(Lod) || Lod < 0 || Lod > 4) { Error = TEXT("Invalid lod"); return false; }
                        Entity.Lod = int32(Lod);
                    }
                    if ((!HasTransform || (*Geometry)->Values.IsEmpty()) && Template.IsEmpty() && !Operation->HasField(TEXT("collision")) && !Operation->HasField(TEXT("lod"))) { Error = TEXT("Empty entity patch"); return false; }
                    if (HasTransform) for (const auto& Field : (*Geometry)->Values)
                    {
                        const FString Key(Field.Key);
                        FVector* Destination = Key == TEXT("position_cm") ? &Entity.Position : Key == TEXT("size_cm") ? &Entity.Size : Key == TEXT("rotation_deg") ? &Entity.Rotation : nullptr;
                        if (!Destination || !LCReadVector(*Geometry, Key, *Destination)) { Error = TEXT("Invalid transform patch"); return false; }
                    }
                }
                if (Verb == TEXT("create") && Operation->HasField(TEXT("snap")))
                {
                    bool Snap;
                    if (!Operation->TryGetBoolField(TEXT("snap"), Snap)) { Error = TEXT("snap must be boolean"); return false; }
                    if (Snap)
                    {
                        auto* Player = UGameplayStatics::GetPlayerCharacter(Host->GetWorld(), 0);
                        FCollisionQueryParams Query(SCENE_QUERY_STAT(LCPlacementSnap), false, Player);
                        FHitResult Hit;
                        if (!Host->GetWorld()->LineTraceSingleByChannel(Hit, Entity.Position + FVector(0,0,5000), Entity.Position - FVector(0,0,10000), ECC_Visibility, Query))
                        { Error = TEXT("No supporting surface under placement: ") + Id; return false; }
                        Entity.Position.Z = Hit.ImpactPoint.Z + Entity.Bounds().GetExtent().Z;
                    }
                }
                Entity.Version = Pending.Version;
                FLCEntity Checked;
                if (!FLCEntity::FromJson(Entity.ToJson(), Checked, Error)) return false;
                if (Entity.Collision && OverlapsPlayer(Entity)) { Error = TEXT("Entity bounds overlap player capsule: ") + Id; return false; }
                const FString Key = LCBatchKey(Entity);
                if (!Entity.Template.StartsWith(TEXT("decal/")) && !Batches.Contains(Key))
                {
                    auto* Batch = LCCreateBatch(Host, Entity, false, Error);
                    if (!Batch) return false;
                    Batches.Add(Key, Batch);
                }
                After = Entity.ToJson();
            }
            Pending.Changes.Add({Id, Old ? Old->ToJson() : nullptr, After});
        }
    }
    if (Deletes > 1)
    {
        Pending.CheckpointName = TEXT("auto-") + Pending.TransactionId;
        Pending.CheckpointSnapshot = Snapshot();
    }
    return true;
}

TSharedPtr<FJsonObject> ULCTxService::Submit(const TSharedPtr<FJsonObject>& Request, bool Undo, const FString& Mode, const FString& TransactionId)
{
    FString RequestId;
    if (!Request || !Request->TryGetStringField(TEXT("req_id"), RequestId) || RequestId.IsEmpty() || RequestId.Len() > 128)
        return MakeReceipt(TEXT(""), TEXT("rejected"), TEXT("req_id must contain 1..128 characters"));
    if (Receipts.Contains(RequestId)) return Receipts[RequestId];
    if (IsBusy() || DesignPreview || (EnvironmentPreview && Mode != TEXT("environment")))
    {
        auto Rejected = MakeReceipt(RequestId, TEXT("rejected"), TEXT("Transaction service is busy; retry with a new req_id"));
        Receipts.Add(RequestId, Rejected);
        return Rejected;
    }
    if (Request->HasField(TEXT("expected_version")))
    {
        int64 Expected;
        if (!Request->TryGetNumberField(TEXT("expected_version"), Expected) || Expected != Version)
        {
            auto Rejected = MakeReceipt(RequestId, TEXT("rejected"), TEXT("Stale expected_version; reread the world"));
            Receipts.Add(RequestId, Rejected); return Rejected;
        }
    }
    Started = FPlatformTime::Seconds();
    Pending = FLCStoreEdit();
    Pending.RequestId = RequestId;
    Pending.TransactionId = TransactionId.IsEmpty() ? TEXT("tx-") + FGuid::NewGuid().ToString(EGuidFormats::Digits) : TransactionId;
    Pending.Version = Version + 1;
    Pending.CheckpointVersion = Version;
    PendingMode = Mode;
    FString Error;
    LockedSelection.Reset();
    FString Selection;
    bool ValidSelection = true;
    if (Request->TryGetStringField(TEXT("selection_id"), Selection))
    {
        if (!Mode.IsEmpty() || Undo || !Request->HasField(TEXT("expected_version"))) { Error = TEXT("Selection requires an edit and expected_version"); ValidSelection = false; }
        else ValidSelection = SemanticWorld.ResolveSelection(Selection, Version, LockedSelection, Error);
    }
    if (!ValidSelection || !(Mode.IsEmpty() ? Plan(Request, Undo, Error) : Mode == TEXT("annotations") ? PlanAnnotations(Request, Error) : Mode == TEXT("action") ? PlanAction(Request, Error) : Mode == TEXT("environment") ? PlanEnvironment(Request, Error) : PlanSnapshot(Request, Mode, Error)) || !PlanSemanticState(Request, Mode, Undo, Error))
    {
        auto Rejected = MakeReceipt(RequestId, TEXT("rejected"), Error.IsEmpty() ? TEXT("Invalid request fields") : Error);
        Rejected->SetStringField(TEXT("tx_id"), Pending.TransactionId);
        Receipts.Add(RequestId, Rejected);
        return Rejected;
    }
    Pending.Receipt = MakeReceipt(RequestId, TEXT("scheduled"));
    if (Mode == TEXT("annotations")) Pending.Receipt->SetArrayField(TEXT("identity_ids"), Request->GetArrayField(TEXT("ids")));
    Pending.Receipt->SetStringField(TEXT("tx_id"), Pending.TransactionId);
    Receipts.Add(RequestId, Pending.Receipt);
    if (Mode == TEXT("environment")) { EnvironmentPreview.Reset(); EnvironmentRate = 0; }
    Phase = EPhase::Apply;
    Cursor = 0;
    FirstVisible = 0;
    MaxFrameMs = 0;
    Failure.Reset();
    if (Mode == TEXT("action"))
    {
        Phase = EPhase::Animate;
        if (!Pending.CheckpointName.IsEmpty())
        {
            FLCStoreEdit Checkpoint;
            Checkpoint.RequestId = RequestId + TEXT("-checkpoint"); Checkpoint.TransactionId = TEXT("checkpoint-") + Pending.TransactionId;
            Checkpoint.Kind = TEXT("checkpoint"); Checkpoint.Command = TEXT("automatic action checkpoint");
            Checkpoint.Version = Version; Checkpoint.CheckpointVersion = Version;
            Checkpoint.CheckpointName = Pending.CheckpointName; Checkpoint.CheckpointSnapshot = Pending.CheckpointSnapshot;
            Checkpoint.Receipt = MakeReceipt(Checkpoint.RequestId, TEXT("applied"));
            Checkpoint.Receipt->SetStringField(TEXT("tx_id"), Checkpoint.TransactionId);
            Pending.Receipt->SetStringField(TEXT("checkpoint"), Pending.CheckpointName);
            Pending.CheckpointName.Reset(); Pending.CheckpointSnapshot.Reset();
            Phase = EPhase::PreAction;
            StoreJob = Async(EAsyncExecution::ThreadPool, [this, Checkpoint] { return Store.Commit(Checkpoint); });
        }
    }
    return Pending.Receipt;
}

bool ULCTxService::WriteState(const FString& Id, const TSharedPtr<FJsonObject>& State)
{
    if (Id == TEXT("@semantics")) { SemanticState = State ? LCParseJson(LCJsonString(State)) : LCEmptySemanticState(); return true; }
    if (Id == TEXT("@environment")) { FString Error; return WriteEnvironment(State, Error); }
    if (!State) { Remove(Id); return true; }
    FLCEntity Entity;
    FString Error, Handle;
    if (!FLCEntity::FromJson(State, Entity, Error)) return false;
    if (Decals.Contains(Id)) Remove(Id);
    if (auto* Instance = Instances.Find(Id))
    {
        if (Instance->Template == LCBatchKey(Entity))
        {
            Batches[Instance->Template]->UpdateInstanceTransformById(Instance->Handle, Entity.Transform(), true, true);
            Entities.Add(Id, Entity);
            SemanticWorld.Observe(Id, Entity.ToJson()->GetObjectField(TEXT("transform")), TEXT("applying"));
            return true;
        }
        Remove(Id);
    }
    const bool Created = Create(Entity, Handle, Error);
    if (Created) SemanticWorld.Observe(Id, Entity.ToJson()->GetObjectField(TEXT("transform")), TEXT("applying"));
    return Created;
}

void ULCTxService::Finish(const FString& Status, const FString& Warning)
{
    auto Result = LCParseJson(LCJsonString(Pending.Receipt));
    Result->SetStringField(TEXT("status"), Status);
    Result->SetNumberField(TEXT("version"), Version);
    int32 Applied = 0; for (const auto& Change : Pending.Changes) if (!Change.Id.StartsWith(TEXT("@"))) ++Applied;
    Result->SetNumberField(TEXT("applied"), Status == TEXT("applied") ? Applied : 0);
    Result->SetNumberField(TEXT("ms"), (FPlatformTime::Seconds() - Started) * 1000.0);
    Result->SetNumberField(TEXT("max_apply_frame_ms"), MaxFrameMs);
    Result->SetNumberField(TEXT("first_visible_game_frame_s"), FirstVisible);
    Result->SetNumberField(TEXT("commit_s"), (FPlatformTime::Seconds() - Started));
    Result->SetField(TEXT("presentation_s"), MakeShared<FJsonValueNull>());
    if (!Warning.IsEmpty()) Result->SetArrayField(TEXT("warnings"), {MakeShared<FJsonValueString>(Warning)});
    Receipts.Add(Pending.RequestId, Result);
    if (PendingMode == TEXT("reconcile") && Status == TEXT("failed")) Presentation->Cancel();
    if (Status == TEXT("failed")) SemanticWorld.Publish(CommittedEntities, CommittedSemanticState, Version);
    UE_LOG(LogLC, Display, TEXT("%s %s count=%d ms=%.2f"), *Pending.TransactionId, *Status, Pending.Changes.Num(), (FPlatformTime::Seconds() - Started) * 1000.0);
    Phase = EPhase::Idle;
}

void ULCTxService::Tick(float DeltaTime)
{
    SemanticWorld.Tick();
    const FLCFrameSample Sample = {++FrameSequence, double(DeltaTime) * 1000, Version, IsBusy(), Phase == EPhase::Capture};
    if (FrameHistory.Num() < 3600) FrameHistory.Add(Sample);
    else FrameHistory[(FrameSequence - 1) % 3600] = Sample;
    if (Phase == EPhase::Measure) { TickMeasurement(DeltaTime); return; }
    if (EnvironmentPreview && EnvironmentRate > 0)
    {
        auto Patch = MakeShared<FJsonObject>();
        Patch->SetNumberField(TEXT("time_h"), FMath::Fmod(EnvironmentPreview->GetNumberField(TEXT("time_h")) + DeltaTime * EnvironmentRate, 24));
        TSharedPtr<FJsonObject> Next; FString Error;
        if (ULCEnvironment::Patch(EnvironmentPreview, Patch, Next, Error) && EnvironmentRenderer->Apply(Next, Error)) EnvironmentPreview = Next;
    }
    if (Presentation) Presentation->Tick(DeltaTime);
    if (QueuedCommit && Presentation->IsCancelled()) CancelQueued();
    if (Phase == EPhase::Animate || Phase == EPhase::PreAction) { TickAction(); return; }
    if (Phase == EPhase::Idle && QueuedCommit && Presentation->IsSettled() && FPlatformTime::Seconds() >= HoldDeadline())
    {
        auto Request = QueuedCommit; QueuedCommit.Reset();
        const FString TransactionId = Receipts[Request->GetStringField(TEXT("req_id"))]->GetStringField(TEXT("tx_id"));
        Receipts.Remove(Request->GetStringField(TEXT("req_id")));
        auto Result = Submit(Request, false, TEXT("reconcile"), TransactionId);
        if (Result->GetStringField(TEXT("status")) == TEXT("rejected")) Presentation->Cancel();
    }
    if (Phase == EPhase::Idle && SelectionVersion != Version) RefreshSelection();
    if (Phase == EPhase::Idle)
    {
        for (auto It = Batches.CreateIterator(); It; ++It)
            if (It.Key().Contains(TEXT("|")) && It.Value()->GetInstanceCount() == 0)
            { It.Value()->DestroyComponent(); It.RemoveCurrent(); }
        return;
    }
    if (Phase == EPhase::Capture)
    {
        if (FPlatformTime::Seconds() - Started > 20)
        { ReturnReviewCamera(); Finish(TEXT("failed"), TEXT("Screenshot timed out; a rendered game viewport is required")); return; }
        if (CaptureDelayFrames > 0)
        { if (--CaptureDelayFrames == 0) BeginScreenshot(); return; }
        if (!CaptureJob.IsValid() || !CaptureJob.IsReady()) return;
        const bool Saved = CaptureJob.Get(); CaptureJob = TFuture<bool>();
        if (!Saved) { ReturnReviewCamera(); Finish(TEXT("failed"), TEXT("Screenshot encoding or save failed")); return; }
        CapturedViews.Add(CaptureViews[CaptureIndex++]);
        if (CaptureIndex < CaptureViews.Num())
        {
            FVector Position, Target;
            const auto View = CaptureViews[CaptureIndex]->AsObject();
            LCReadVector(View, TEXT("position_cm"), Position); LCReadVector(View, TEXT("target_cm"), Target);
            ReviewCamera->SetActorLocation(Position); ReviewCamera->SetActorRotation((Target - Position).Rotation());
            CaptureDelayFrames = 3; return;
        }
        ReturnReviewCamera();
        Pending.Receipt = LCParseJson(LCJsonString(Pending.Receipt));
        Pending.Receipt->SetStringField(TEXT("status"), TEXT("applied"));
        Pending.Receipt->SetArrayField(TEXT("captures"), CapturedViews);
        Phase = EPhase::Persist;
        StoreJob = Async(EAsyncExecution::ThreadPool, [this] { return Store.Commit(Pending); });
        return;
    }
    if (Phase == EPhase::Persist || Phase == EPhase::Finalize)
    {
        if (!StoreJob.IsReady()) return;
        const bool Success = StoreJob.Get();
        if (Phase == EPhase::Finalize)
        {
            if (!Success) Pending.Receipt->SetArrayField(TEXT("warnings"), {MakeShared<FJsonValueString>(TEXT("Receipt timing update failed; entity commit is durable"))});
            Receipts.Add(Pending.RequestId, Pending.Receipt);
            UE_LOG(LogLC, Display, TEXT("%s applied count=%d ms=%.2f"), *Pending.TransactionId, Pending.Changes.Num(), Pending.Receipt->GetNumberField(TEXT("ms")));
            Phase = EPhase::Idle;
            if (PendingMode == TEXT("reconcile")) Presentation->Committed();
            return;
        }
        if (!Success) { Failure = TEXT("SQLite commit failed: ") + Store.Error; Phase = EPhase::Rollback; Cursor = Pending.Changes.Num() - 1; }
        else
        {
            Version = Pending.Version;
            CommittedEntities = Entities;
            CommittedSemanticState = LCParseJson(LCJsonString(SemanticState));
            SemanticWorld.Publish(CommittedEntities, CommittedSemanticState, Version);
            auto Final = LCParseJson(LCJsonString(Pending.Receipt));
            Final->SetNumberField(TEXT("ms"), (FPlatformTime::Seconds() - Started) * 1000.0);
            Final->SetNumberField(TEXT("commit_s"), FPlatformTime::Seconds() - Started);
            Final->SetField(TEXT("presentation_s"), MakeShared<FJsonValueNull>());
            Pending.Receipt = Final;
            Phase = EPhase::Finalize;
            StoreJob = Async(EAsyncExecution::ThreadPool, [this, Final] { return Store.UpdateReceipt(Pending.RequestId, Final); });
            return;
        }
    }
    const double FrameStart = FPlatformTime::Seconds();
    // Reserve time for the final instance operation; record the measured cost as well.
    while (FPlatformTime::Seconds() - FrameStart < 0.00075)
    {
        if (Phase == EPhase::Rollback)
        {
            if (Cursor < 0) { Finish(TEXT("failed"), Failure); return; }
            const auto& Change = Pending.Changes[Cursor--];
            if (!WriteState(Change.Id, Change.Before)) UE_LOG(LogLC, Error, TEXT("Rollback render state failed for %s"), *Change.Id);
            continue;
        }
        if (Cursor >= Pending.Changes.Num()) break;
        const auto& Change = Pending.Changes[Cursor];
        if (Change.After && !Change.Id.StartsWith(TEXT("@")))
        {
            FLCEntity Entity;
            FString Error;
            FLCEntity::FromJson(Change.After, Entity, Error);
            if (OverlapsPlayer(Entity))
            { Failure = TEXT("Player moved into pending geometry"); Phase = EPhase::Rollback; --Cursor; continue; }
        }
        if (!WriteState(Change.Id, Change.After)) { Failure = TEXT("Failed to apply entity state"); Phase = EPhase::Rollback; continue; }
        if (PendingMode == TEXT("reconcile")) Presentation->Handoff(Change.Id);
        if (Cursor == 0) FirstVisible = FPlatformTime::Seconds() - Started;
        ++Cursor;
    }
    MaxFrameMs = FMath::Max(MaxFrameMs, (FPlatformTime::Seconds() - FrameStart) * 1000.0);
    if (Phase == EPhase::Apply && Cursor == Pending.Changes.Num())
    {
        auto Final = LCParseJson(LCJsonString(Pending.Receipt));
        Final->SetStringField(TEXT("status"), TEXT("applied"));
        int32 Applied = 0; for (const auto& Change : Pending.Changes) if (!Change.Id.StartsWith(TEXT("@"))) ++Applied;
        Final->SetNumberField(TEXT("applied"), Applied);
        Final->SetNumberField(TEXT("version"), Pending.Version);
        Final->SetNumberField(TEXT("ms"), (FPlatformTime::Seconds() - Started) * 1000.0);
        TArray<TSharedPtr<FJsonValue>> Ids;
        for (const auto& Change : Pending.Changes) if (!Change.Id.StartsWith(TEXT("@"))) Ids.Add(MakeShared<FJsonValueString>(Change.Id));
        Final->SetArrayField(TEXT("entity_ids"), Ids);
        Final->SetNumberField(TEXT("max_apply_frame_ms"), MaxFrameMs);
        Final->SetNumberField(TEXT("first_visible_game_frame_s"), FirstVisible);
        Pending.Receipt = Final;
        Phase = EPhase::Persist;
        StoreJob = Async(EAsyncExecution::ThreadPool, [this] { return Store.Commit(Pending); });
    }
}

TSharedPtr<FJsonObject> ULCTxService::Consistency()
{
    auto Result = MakeShared<FJsonObject>();
    if (IsBusy()) { Result->SetBoolField(TEXT("ok"), false); Result->SetStringField(TEXT("status"), TEXT("busy")); return Result; }
    TMap<FString, FLCEntity> Stored;
    TMap<FString, TSharedPtr<FJsonObject>> StoredReceipts;
    int64 StoredVersion;
    TSharedPtr<FJsonObject> StoredEnvironment;
    bool Matches = Store.Load(Stored, StoredReceipts, StoredVersion, &StoredEnvironment) && Stored.Num() == Entities.Num() && StoredVersion == Version;
    for (const auto& Item : Entities)
    {
        const auto* Other = Stored.Find(Item.Key);
        if (!Other || LCJsonString(Item.Value.ToJson()) != LCJsonString(Other->ToJson())) Matches = false;
    }
    if (StoredEnvironment && LCJsonString(StoredEnvironment) != LCJsonString(Environment)) Matches = false;
    Result->SetBoolField(TEXT("ok"), Matches && GetInstanceCount() == Entities.Num());
    Result->SetNumberField(TEXT("memory_count"), Entities.Num());
    Result->SetNumberField(TEXT("sqlite_count"), Stored.Num());
    Result->SetNumberField(TEXT("instance_count"), GetInstanceCount());
    Result->SetNumberField(TEXT("version"), Version);
    return Result;
}

void ULCTxService::RefreshSelection()
{
    TArray<FBox> Boxes;
    for (const auto& Id : SelectedIds) if (const auto* Entity = Entities.Find(Id)) Boxes.Add(Entity->Bounds().ExpandBy(1));
    if (Indicator) Indicator->SetBoxes(Boxes);
    SelectionVersion = Version;
}

TSharedPtr<FJsonObject> ULCTxService::Select(const TSharedPtr<FJsonObject>& Request)
{
    FString Id, Selector, Error;
    if (!Request || !Request->TryGetStringField(TEXT("req_id"), Id) || Id.IsEmpty() || Id.Len() > 128)
        return MakeReceipt(TEXT(""), TEXT("rejected"), TEXT("req_id is required"));
    if (Receipts.Contains(Id)) return Receipts[Id];
    if (IsBusy()) return MakeReceipt(Id, TEXT("rejected"), TEXT("Transaction service is busy"));
    bool Nearest = false;
    Request->TryGetBoolField(TEXT("nearest"), Nearest);
    const auto* Player = UGameplayStatics::GetPlayerCharacter(Host->GetWorld(), 0);
    TArray<FString> Chosen;
    if (!Request->TryGetStringField(TEXT("selector"), Selector) ||
        !LCResolve(Selector, Entities, Player ? Player->GetActorLocation() : FVector::ZeroVector, Nearest, false, Chosen, Error))
        return MakeReceipt(Id, TEXT("rejected"), Error.IsEmpty() ? TEXT("selector is required") : Error);
    const double Start = FPlatformTime::Seconds();
    SelectedIds = Chosen;
    RefreshSelection();
    auto Result = MakeReceipt(Id, TEXT("applied"));
    Result->SetStringField(TEXT("tx_id"), TEXT("select-") + FGuid::NewGuid().ToString(EGuidFormats::Digits));
    Result->SetNumberField(TEXT("applied"), Chosen.Num());
    TArray<TSharedPtr<FJsonValue>> Values;
    for (const auto& EntityId : Chosen) Values.Add(MakeShared<FJsonValueString>(EntityId));
    Result->SetArrayField(TEXT("entity_ids"), Values);
    Result->SetNumberField(TEXT("ms"), (FPlatformTime::Seconds() - Start) * 1000.0);
    Receipts.Add(Id, Result);
    return Result;
}

