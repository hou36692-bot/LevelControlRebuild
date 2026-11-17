#include "LCTxService.h"
#include "LCPresentation.h"

bool ULCTxService::PlanSnapshot(const TSharedPtr<FJsonObject>& Request, const FString& Mode, FString& Error)
{
    TMap<FString, FLCEntity> Desired;
    FString Source, Name, Action;
    if (Mode == TEXT("reconcile"))
    {
        FString ReconcileMode; int64 Expected;
        if (!Request->TryGetNumberField(TEXT("expected_version"), Expected) || Expected != Version ||
            !Request->TryGetStringField(TEXT("mode"), ReconcileMode) || ReconcileMode != TEXT("reconcile"))
        { Error = TEXT("Stale expected_version or invalid reconcile mode"); return false; }
        if (!Presentation->Matches(Request, Error)) return false;
        Source = Request->GetStringField(TEXT("source"));
        if (Request->HasField(TEXT("environment")))
        {
            const TSharedPtr<FJsonObject>* Patch = nullptr;
            if (!Request->TryGetObjectField(TEXT("environment"), Patch))
            { Error = TEXT("environment must be an object"); return false; }
            for (const auto& Field : (*Patch)->Values)
                if (Field.Key != TEXT("preset") && Field.Key != TEXT("values"))
                { Error = TEXT("Unexpected commit environment field: ") + FString(Field.Key); return false; }
            auto EnvironmentRequest = LCParseJson(LCJsonString(*Patch));
            EnvironmentRequest->SetStringField(TEXT("action"), TEXT("apply"));
            if (!PlanEnvironment(EnvironmentRequest, Error)) return false;
        }
        Pending.Command = TEXT("commit ") + Source;
        FString OverridePolicy; Request->TryGetStringField(TEXT("override_policy"), OverridePolicy);
        for (const auto& Value : Request->GetArrayField(TEXT("entities")))
        {
            FLCEntity Entity;
            if (!FLCEntity::FromJson(Value->AsObject(), Entity, Error)) return false;
            Entity.Source = Source;
            if (SemanticWorld.Rows.Contains(Entity.Id) && !Entities.Contains(Entity.Id))
            { Error = TEXT("Entity id is reserved by another identity: ") + Entity.Id; return false; }
            if (OverridePolicy != TEXT("discard"))
            {
                const auto Overrides = SemanticState->GetObjectField(TEXT("overrides"));
                const TSharedPtr<FJsonObject>* Override;
                if (Overrides->TryGetObjectField(Entity.Id, Override))
                { bool Deleted = false; (*Override)->TryGetBoolField(TEXT("deleted"), Deleted); if (Deleted) continue; }
                if (!ApplyInstanceOverride(Entity, Error)) return false;
            }
            if (const auto* Existing = Entities.Find(Entity.Id))
                if (Existing->Source != Source) { Error = TEXT("Entity id belongs to another source: ") + Entity.Id; return false; }
            Desired.Add(Entity.Id, Entity);
        }
        if (OverridePolicy != TEXT("discard")) for (const auto& Pair : Entities)
        {
            const TSharedPtr<FJsonObject>* Override; bool Added = false;
            if (Pair.Value.Source == Source && SemanticState->GetObjectField(TEXT("overrides"))->TryGetObjectField(Pair.Key, Override) && (*Override)->TryGetBoolField(TEXT("added"), Added) && Added)
                Desired.FindOrAdd(Pair.Key) = Pair.Value;
        }
    }
    else
    {
        if (!Request->TryGetStringField(TEXT("name"), Name) || !LCValidId(Name) ||
            !Request->TryGetStringField(TEXT("action"), Action) || !(Action == TEXT("save") || Action == TEXT("restore")))
        { Error = TEXT("Use action save/restore and a kebab-case checkpoint name"); return false; }
        Pending.Command = TEXT("checkpoint ") + Action + TEXT(" ") + Name;
        if (Action == TEXT("save"))
        {
            const auto List = Store.Checkpoints();
            if (List->HasField(TEXT("error"))) { Error = TEXT("Cannot query checkpoints"); return false; }
            for (const auto& Value : List->GetArrayField(TEXT("checkpoints")))
                if (Value->AsObject()->GetStringField(TEXT("name")) == Name) { Error = TEXT("Checkpoint name already exists"); return false; }
            Pending.Kind = TEXT("checkpoint"); Pending.Version = Version; Pending.CheckpointName = Name;
            Pending.CheckpointSnapshot = Snapshot();
            return true;
        }
        TSharedPtr<FJsonObject> RestoredEnvironment, RestoredSemantic;
        if (!Store.Checkpoint(Name, Desired, &RestoredEnvironment, &RestoredSemantic)) { Error = Store.Error; return false; }
        if (RestoredEnvironment) Pending.Changes.Add({TEXT("@environment"), Environment, RestoredEnvironment});
        Pending.Changes.Add({TEXT("@semantics"), SemanticState, RestoredSemantic});
    }
    TSet<FString> Ids;
    for (const auto& Item : Desired) Ids.Add(Item.Key);
    for (const auto& Item : Entities) if (Mode != TEXT("reconcile") || Item.Value.Source == Source) Ids.Add(Item.Key);
    TArray<FString> Ordered = Ids.Array(); Ordered.Sort();
    int32 Deletes = 0;
    for (const auto& Id : Ordered)
    {
        const auto* Before = Entities.Find(Id);
        auto* After = Desired.Find(Id);
        if (After)
        {
            After->Version = Before ? Before->Version : 0;
            if (Before && LCJsonString(Before->ToJson()) == LCJsonString(After->ToJson())) continue;
            if (OverlapsPlayer(*After)) { Error = TEXT("Restored/committed geometry overlaps player: ") + Id; return false; }
            After->Version = Pending.Version;
        }
        else ++Deletes;
        Pending.Changes.Add({Id, Before ? Before->ToJson() : nullptr, After ? After->ToJson() : nullptr});
        const int32 EnvironmentCount = Pending.Changes[0].Id == TEXT("@environment") ? 1 : 0;
        if (Pending.Changes.Num() - EnvironmentCount > 16384) { Error = TEXT("batch_limit is 16384 changed entities"); return false; }
    }
    if (Pending.Changes.IsEmpty()) { Pending.Kind = TEXT("receipt"); Pending.Version = Version; }
    if (Deletes > 1 || Action == TEXT("restore"))
    {
        Pending.CheckpointName = TEXT("auto-") + Pending.TransactionId;
        Pending.CheckpointSnapshot = Snapshot();
    }
    return true;
}

static bool LCRequestId(const TSharedPtr<FJsonObject>& Request, FString& Id)
{
    return Request && Request->TryGetStringField(TEXT("req_id"), Id) && !Id.IsEmpty() && Id.Len() <= 128;
}

void ULCTxService::CancelQueued()
{
    if (!QueuedCommit) return;
    const FString Id = QueuedCommit->GetStringField(TEXT("req_id"));
    auto Result = MakeReceipt(Id, TEXT("rejected"), TEXT("Preview was replaced or cancelled before entity commit"));
    Result->SetStringField(TEXT("tx_id"), Receipts[Id]->GetStringField(TEXT("tx_id")));
    Receipts.Add(Id, Result); QueuedCommit.Reset();
}

TSharedPtr<FJsonObject> ULCTxService::Preview(const TSharedPtr<FJsonObject>& Request)
{
    FString Id, Error;
    if (!LCRequestId(Request, Id)) return MakeReceipt(TEXT(""), TEXT("rejected"), TEXT("req_id is required"));
    if (Receipts.Contains(Id)) return Receipts[Id];
    TSharedPtr<FJsonObject> Result;
    if (IsBusy() || EnvironmentPreview || DesignPreview) Result = MakeReceipt(Id, TEXT("rejected"), TEXT("Entity transaction is applying; retry after receipt"));
    else
    {
        const double Start = FPlatformTime::Seconds();
        if (!Presentation->Publish(Request, Error)) Result = MakeReceipt(Id, TEXT("rejected"), Error);
        else
        {
            CancelQueued();
            if (Presentation->ShouldAutoSkip() && FPlatformTime::Seconds() < HoldDeadline())
                Presentation->Skip(TEXT("input_held"));
            Result = MakeReceipt(Id, TEXT("applied"));
            Result->SetStringField(TEXT("tx_id"), TEXT("preview-") + FGuid::NewGuid().ToString(EGuidFormats::Digits));
            Result->SetNumberField(TEXT("revision"), Presentation->Revision);
            Result->SetNumberField(TEXT("ms"), (FPlatformTime::Seconds() - Start) * 1000);
        }
    }
    Receipts.Add(Id, Result); return Result;
}

TSharedPtr<FJsonObject> ULCTxService::Commit(const TSharedPtr<FJsonObject>& Request)
{
    FString Id, Error;
    if (!LCRequestId(Request, Id)) return MakeReceipt(TEXT(""), TEXT("rejected"), TEXT("req_id is required"));
    if (Receipts.Contains(Id)) return Receipts[Id];
    if (IsBusy() || QueuedCommit || EnvironmentPreview || DesignPreview) Error = TEXT("A commit is already active");
    else if (!Presentation->Matches(Request, Error)) {}
    else
    {
        double Expected;
        FString Mode;
        if (!Request->TryGetNumberField(TEXT("expected_version"), Expected) || Expected != Version ||
            !Request->TryGetStringField(TEXT("mode"), Mode) || Mode != TEXT("reconcile")) Error = TEXT("Stale expected_version or invalid mode");
    }
    auto Result = MakeReceipt(Id, Error.IsEmpty() ? TEXT("scheduled") : TEXT("rejected"), Error);
    if (Error.IsEmpty())
    {
        Result->SetStringField(TEXT("tx_id"), TEXT("tx-") + FGuid::NewGuid().ToString(EGuidFormats::Digits));
        QueuedCommit = Request;
    }
    Receipts.Add(Id, Result); return Result;
}

TSharedPtr<FJsonObject> ULCTxService::Control(const TSharedPtr<FJsonObject>& Request)
{
    FString Id, Action, Error, Lease;
    if (!LCRequestId(Request, Id)) return MakeReceipt(TEXT(""), TEXT("rejected"), TEXT("req_id is required"));
    if (Receipts.Contains(Id)) return Receipts[Id];
    Request->TryGetStringField(TEXT("action"), Action);
    Request->TryGetStringField(TEXT("lease_id"), Lease);
    if (!Lease.IsEmpty() && !LCValidId(Lease)) Error = TEXT("Invalid lease_id");
    else if (Action == TEXT("skip"))
    {
        double Revision;
        if (Request->HasField(TEXT("revision")) &&
            (!Request->TryGetNumberField(TEXT("revision"), Revision) || Revision != Presentation->Revision))
            Error = TEXT("Stale presentation revision");
        else if (!Presentation->Skip()) Error = TEXT("No active construction presentation");
    }
    else if (Action == TEXT("camera-player")) Presentation->ReturnCamera();
    else if (Action == TEXT("hold") && !IsBusy())
    {
        for (auto It = HoldLeases.CreateIterator(); It; ++It) if (It.Value() <= FPlatformTime::Seconds()) It.RemoveCurrent();
        if (Lease.IsEmpty()) CommitHeldUntil = FPlatformTime::Seconds() + 120;
        else if (HoldLeases.Num() < 32 || HoldLeases.Contains(Lease)) HoldLeases.Add(Lease, FPlatformTime::Seconds() + 120);
        else Error = TEXT("Too many active hold leases");
        if (Error.IsEmpty() && Presentation->ShouldAutoSkip()) Presentation->Skip(TEXT("input_held"));
    }
    else if (Action == TEXT("resume")) { if (Lease.IsEmpty()) CommitHeldUntil = 0; else HoldLeases.Remove(Lease); }
    else if (Action != TEXT("cancel")) Error = TEXT("Use action skip, cancel, camera-player, hold or resume");
    else if (IsBusy()) Error = TEXT("An entity transaction is already applying; wait for its receipt then undo");
    else { CancelDesign(); CancelQueued(); Presentation->Cancel(); HoldLeases.Reset(); CommitHeldUntil = 0; }
    auto Result = MakeReceipt(Id, Error.IsEmpty() ? TEXT("applied") : TEXT("rejected"), Error);
    Result->SetStringField(TEXT("tx_id"), TEXT("control-") + FGuid::NewGuid().ToString(EGuidFormats::Digits));
    Receipts.Add(Id, Result); return Result;
}

TSharedPtr<FJsonObject> ULCTxService::PresentationStatus() const
{
    auto Result = Presentation->Status();
    Result->SetBoolField(TEXT("review_camera_active"), ReviewCamera != nullptr);
    Result->SetBoolField(TEXT("design_preview_active"), DesignPreview.IsValid());
    Result->SetNumberField(TEXT("design_preview_version"), DesignPreviewVersion);
    Result->SetBoolField(TEXT("commit_waiting"), QueuedCommit.IsValid());
    Result->SetBoolField(TEXT("commit_held"), FPlatformTime::Seconds() < HoldDeadline());
    Result->SetStringField(TEXT("action_state"), Phase == EPhase::Animate ? (ActionPausedAt > 0 ? TEXT("paused") : TEXT("running")) : Phase == EPhase::PreAction ? TEXT("checkpoint") : TEXT("idle"));
    Result->SetNumberField(TEXT("action_elapsed_s"), Phase == EPhase::Animate ? (ActionPausedAt > 0 ? ActionPausedAt : FPlatformTime::Seconds()) - ActionStarted : 0);
    return Result;
}
TSharedPtr<FJsonObject> ULCTxService::Events(int64 Since) const { return Presentation->Events(Since); }
double ULCTxService::HoldDeadline() const
{
    double Deadline = CommitHeldUntil;
    for (const auto& Lease : HoldLeases) Deadline = FMath::Max(Deadline, Lease.Value);
    return Deadline;
}
TSharedPtr<FJsonObject> ULCTxService::Checkpoints()
{
    return IsBusy() ? MakeReceipt(TEXT(""), TEXT("rejected"), TEXT("Store is busy")) : Store.Checkpoints();
}
