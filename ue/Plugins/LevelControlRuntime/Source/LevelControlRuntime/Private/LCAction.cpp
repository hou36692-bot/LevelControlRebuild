#include "LCTxService.h"
#include "LCSelector.h"
#include "Async/Async.h"

bool ULCTxService::PlanAction(const TSharedPtr<FJsonObject>& Request, FString& Error)
{
    FString Selector, Kind;
    double Angle = 180, Factor = 1.2, Frequency = 1;
    FVector Delta = FVector::ZeroVector, Amplitude(0, 0, 20);
    if (!Request->TryGetStringField(TEXT("selector"), Selector) || !Request->TryGetStringField(TEXT("kind"), Kind) ||
        !Request->TryGetNumberField(TEXT("duration_s"), ActionDuration) || !FMath::IsFinite(ActionDuration) || ActionDuration < 0.1 || ActionDuration > 30)
    { Error = TEXT("Action requires selector, kind and duration_s within 0.1..30"); return false; }
    const TArray<FString> Kinds = {TEXT("move"), TEXT("spin"), TEXT("scale"), TEXT("oscillate")};
    if (!Kinds.Contains(Kind)) { Error = TEXT("Unsupported action"); return false; }
    Request->TryGetNumberField(TEXT("angle_deg"), Angle); Request->TryGetNumberField(TEXT("factor"), Factor);
    Request->TryGetNumberField(TEXT("frequency_hz"), Frequency);
    if (!FMath::IsFinite(Angle) || FMath::Abs(Angle) > 3600 || !FMath::IsFinite(Factor) || Factor < 0.1 || Factor > 5 ||
        !FMath::IsFinite(Frequency) || Frequency < 0.1 || Frequency > 5)
    { Error = TEXT("Invalid action angle, factor or frequency"); return false; }
    if (Kind == TEXT("move") && !LCReadVector(Request, TEXT("delta_cm"), Delta)) { Error = TEXT("delta_cm is required"); return false; }
    if (Kind == TEXT("oscillate") && !LCReadVector(Request, TEXT("amplitude_cm"), Amplitude)) { Error = TEXT("amplitude_cm is required"); return false; }
    if (Delta.GetAbsMax() > 10000 || Amplitude.GetAbsMax() > 500) { Error = TEXT("Action movement exceeds supported range"); return false; }
    TArray<FString> Ids;
    if (!LCResolve(Selector, Entities, FVector::ZeroVector, false, false, Ids, Error)) return false;
    if (Ids.Num() > 256) { Error = TEXT("Actions support at most 256 entities"); return false; }
    ActionFrom.Reset(); ActionTo.Reset();
    for (const auto& Id : Ids)
    {
        FLCEntity From = Entities[Id], To = From;
        if (From.Template.StartsWith(TEXT("decal/"))) { Error = TEXT("Decals support edits, not geometry animation actions"); return false; }
        if (Kind == TEXT("move")) To.Position += Delta;
        if (Kind == TEXT("spin")) To.Rotation.Y += Angle;
        if (Kind == TEXT("scale")) To.Size *= Factor;
        To.Version = Pending.Version;
        FLCEntity Checked;
        if (!FLCEntity::FromJson(To.ToJson(), Checked, Error)) return false;
        FBox Sweep = From.Bounds(); Sweep += To.Bounds();
        if (Kind == TEXT("oscillate")) Sweep = Sweep.ExpandBy(Amplitude.GetAbs());
        if (Kind == TEXT("spin")) Sweep = FBox(From.Position - FVector(From.Size.Size() / 2), From.Position + FVector(From.Size.Size() / 2));
        FLCEntity Volume = From; Volume.Position = Sweep.GetCenter(); Volume.Size = Sweep.GetSize(); Volume.Rotation = FVector::ZeroVector;
        if (OverlapsPlayer(Volume)) { Error = TEXT("Action sweep overlaps player capsule"); return false; }
        ActionFrom.Add(From); ActionTo.Add(To);
        Pending.Changes.Add({Id, From.ToJson(), To.ToJson()});
    }
    ActionRequest = Request; ActionStarted = FPlatformTime::Seconds(); ActionPausedAt = 0; ActionStopRequested = false; ActionCursor = 0;
    Pending.Command = TEXT("act ") + Selector + TEXT(" ") + Kind;
    if (Kind == TEXT("oscillate"))
    {
        Pending.CheckpointName = TEXT("auto-") + Pending.TransactionId;
        Pending.CheckpointSnapshot = Snapshot();
    }
    return true;
}

void ULCTxService::TickAction()
{
    if (Phase == EPhase::PreAction)
    {
        if (!StoreJob.IsReady()) return;
        if (!StoreJob.Get()) { Finish(TEXT("failed"), TEXT("Automatic checkpoint failed: ") + Store.Error); return; }
        ActionStarted = FPlatformTime::Seconds(); Phase = EPhase::Animate;
    }
    if (ActionStopRequested)
    { Failure = TEXT("Action cancelled; original state restored"); Phase = EPhase::Rollback; Cursor = Pending.Changes.Num() - 1; return; }
    if (ActionPausedAt > 0) return;
    const double Time = FPlatformTime::Seconds() - ActionStarted;
    if (Time >= ActionDuration) { Phase = EPhase::Apply; Cursor = 0; return; }
    const double FrameStart = FPlatformTime::Seconds();
    const FString Kind = ActionRequest->GetStringField(TEXT("kind"));
    const double Alpha = Time / ActionDuration;
    FVector Amplitude(0, 0, 20); double Frequency = 1;
    LCReadVector(ActionRequest, TEXT("amplitude_cm"), Amplitude); ActionRequest->TryGetNumberField(TEXT("frequency_hz"), Frequency);
    int32 Count = 0;
    while (Count++ < ActionFrom.Num() && FPlatformTime::Seconds() - FrameStart < 0.001)
    {
        const int32 Index = ActionCursor; ActionCursor = (ActionCursor + 1) % ActionFrom.Num();
        FLCEntity Animated = ActionFrom[Index];
        Animated.Position = FMath::Lerp(ActionFrom[Index].Position, ActionTo[Index].Position, Alpha);
        Animated.Rotation = FMath::Lerp(ActionFrom[Index].Rotation, ActionTo[Index].Rotation, Alpha);
        Animated.Size = FMath::Lerp(ActionFrom[Index].Size, ActionTo[Index].Size, Alpha);
        if (Kind == TEXT("oscillate")) Animated.Position += Amplitude * FMath::Sin(Time * Frequency * 2 * PI);
        if (OverlapsPlayer(Animated))
        { Failure = TEXT("Player entered action geometry; original state restored"); Phase = EPhase::Rollback; Cursor = Pending.Changes.Num() - 1; return; }
        const auto& Instance = Instances[Animated.Id];
        Batches[Instance.Template]->UpdateInstanceTransformById(Instance.Handle, Animated.Transform(), true, true);
        SemanticWorld.Observe(Animated.Id, Animated.ToJson()->GetObjectField(TEXT("transform")), TEXT("animating"));
        if (FirstVisible == 0) FirstVisible = FPlatformTime::Seconds() - Started;
    }
    MaxFrameMs = FMath::Max(MaxFrameMs, (FPlatformTime::Seconds() - FrameStart) * 1000);
}

TSharedPtr<FJsonObject> ULCTxService::ActionControl(const TSharedPtr<FJsonObject>& Request)
{
    FString Id, Action, Error;
    if (!Request || !Request->TryGetStringField(TEXT("req_id"), Id) || Id.IsEmpty() || Id.Len() > 128)
        return MakeReceipt(TEXT(""), TEXT("rejected"), TEXT("req_id is required"));
    if (Receipts.Contains(Id)) return Receipts[Id];
    Request->TryGetStringField(TEXT("action"), Action);
    if (Phase != EPhase::Animate && Phase != EPhase::PreAction) Error = TEXT("No controllable action is running");
    else if (Action == TEXT("stop")) ActionStopRequested = true;
    else if (Action == TEXT("pause")) { if (ActionPausedAt == 0) ActionPausedAt = FPlatformTime::Seconds(); }
    else if (Action == TEXT("resume")) { if (ActionPausedAt > 0) ActionStarted += FPlatformTime::Seconds() - ActionPausedAt; ActionPausedAt = 0; }
    else Error = TEXT("Use pause, resume or stop");
    auto Result = MakeReceipt(Id, Error.IsEmpty() ? TEXT("applied") : TEXT("rejected"), Error);
    Result->SetStringField(TEXT("tx_id"), TEXT("action-control-") + FGuid::NewGuid().ToString(EGuidFormats::Digits));
    Receipts.Add(Id, Result); return Result;
}
