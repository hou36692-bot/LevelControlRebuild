#include "LCTxService.h"
#include "LCPresentation.h"
#include "Components/DecalComponent.h"

void ULCTxService::CancelDesign()
{
    if (!DesignPreview) return;
    for (const auto& Id : DesignHiddenIds)
    {
        if (auto* Decal = Decals.Find(Id)) (*Decal)->SetVisibility(true);
        const auto* Instance = Instances.Find(Id);
        const auto* Entity = Entities.Find(Id);
        if (Instance && Entity) Batches[Instance->Template]->UpdateInstanceTransformById(Instance->Handle, Entity->Transform(), true, true);
    }
    DesignHiddenIds.Reset(); DesignPreview.Reset(); DesignPreviewVersion = -1;
    Presentation->Cancel();
}

TSharedPtr<FJsonObject> ULCTxService::DesignControl(const TSharedPtr<FJsonObject>& Request)
{
    FString Id, Action, Error;
    if (!Request || !Request->TryGetStringField(TEXT("req_id"), Id) || Id.IsEmpty() || Id.Len() > 128)
        return MakeReceipt(TEXT(""), TEXT("rejected"), TEXT("req_id must contain 1..128 characters"));
    if (Receipts.Contains(Id)) return Receipts[Id];
    Request->TryGetStringField(TEXT("action"), Action);
    if (IsBusy() || EnvironmentPreview || QueuedCommit) Error = TEXT("Finish the active transaction or environment preview first");
    const FString State = Presentation->Status()->GetStringField(TEXT("state"));
    if (!DesignPreview && (State == TEXT("previewing") || State == TEXT("settled") || State == TEXT("committed")))
        Error = TEXT("Finish or cancel the active construction presentation first");
    if (Error.IsEmpty() && Action == TEXT("commit"))
    {
        int64 Expected = -1;
        if (!DesignPreview || !Request->TryGetNumberField(TEXT("expected_version"), Expected) || Expected != Version || Expected != DesignPreviewVersion)
            Error = TEXT("No design preview or stale expected_version; read presentation first");
        else
        {
            auto Commit = LCParseJson(LCJsonString(DesignPreview));
            Commit->SetStringField(TEXT("req_id"), Id);
            CancelDesign();
            return Submit(Commit);
        }
    }
    else if (Error.IsEmpty() && Action == TEXT("cancel")) CancelDesign();
    else if (Error.IsEmpty() && Action == TEXT("preview"))
    {
        const auto SavedPending = Pending;
        Pending = FLCStoreEdit(); Pending.Version = Version + 1;
        if (Plan(Request, false, Error))
        {
            auto Preview = MakeShared<FJsonObject>();
            Preview->SetStringField(TEXT("source"), TEXT("design-preview"));
            Preview->SetNumberField(TEXT("revision"), Presentation->Revision + 1);
            Preview->SetArrayField(TEXT("regions"), {});
            TArray<TSharedPtr<FJsonValue>> Values, Timeline;
            for (const auto& Change : Pending.Changes)
            {
                if (!Change.After) continue;
                Values.Add(MakeShared<FJsonValueObject>(Change.After));
                auto Time = MakeShared<FJsonObject>();
                Time->SetStringField(TEXT("id"), Change.Id); Time->SetNumberField(TEXT("start_s"), 0);
                Time->SetNumberField(TEXT("duration_s"), 0.05);
                Timeline.Add(MakeShared<FJsonValueObject>(Time));
            }
            Preview->SetArrayField(TEXT("entities"), Values); Preview->SetArrayField(TEXT("timeline"), Timeline);
            if (Presentation->Publish(Preview, Error))
            {
                for (const auto& Hidden : DesignHiddenIds)
                    if (const auto* Instance = Instances.Find(Hidden))
                        Batches[Instance->Template]->UpdateInstanceTransformById(Instance->Handle, Entities[Hidden].Transform(), true, true);
                for (const auto& Hidden : DesignHiddenIds) if (auto* Decal = Decals.Find(Hidden)) (*Decal)->SetVisibility(true);
                DesignHiddenIds.Reset();
                for (const auto& Change : Pending.Changes)
                    if (const auto* Instance = Instances.Find(Change.Id))
                    {
                        auto Hidden = Entities[Change.Id].Transform(); Hidden.SetScale3D(FVector::ZeroVector);
                        Batches[Instance->Template]->UpdateInstanceTransformById(Instance->Handle, Hidden, true, true);
                        DesignHiddenIds.Add(Change.Id);
                    }
                for (const auto& Change : Pending.Changes)
                    if (auto* Decal = Decals.Find(Change.Id))
                    { (*Decal)->SetVisibility(false); DesignHiddenIds.Add(Change.Id); }
                DesignPreview = LCParseJson(LCJsonString(Request)); DesignPreviewVersion = Version;
            }
        }
        Pending = SavedPending;
    }
    else if (Error.IsEmpty()) Error = TEXT("Use preview, commit or cancel");
    auto Result = MakeReceipt(Id, Error.IsEmpty() ? TEXT("applied") : TEXT("rejected"), Error);
    Result->SetStringField(TEXT("tx_id"), TEXT("preview-") + FGuid::NewGuid().ToString(EGuidFormats::Digits));
    Result->SetNumberField(TEXT("preview_version"), DesignPreviewVersion);
    Receipts.Add(Id, Result); return Result;
}
