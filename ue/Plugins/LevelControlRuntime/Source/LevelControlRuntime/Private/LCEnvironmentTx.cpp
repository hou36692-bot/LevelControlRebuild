#include "LCTxService.h"
#include "LCEnvironment.h"
#include "LCPresentation.h"

FString ULCTxService::Snapshot() const
{
    auto Result = MakeShared<FJsonObject>();
    for (const auto& Item : Entities) Result->SetObjectField(Item.Key, Item.Value.ToJson());
    Result->SetObjectField(TEXT("@environment"), Environment);
    Result->SetObjectField(TEXT("@semantics"), SemanticState);
    return LCJsonString(Result);
}

bool ULCTxService::WriteEnvironment(const TSharedPtr<FJsonObject>& State, FString& Error)
{
    const TSharedPtr<FJsonObject>* Values = nullptr;
    const TSharedPtr<FJsonObject>* Presets = nullptr;
    if (!State || !State->TryGetObjectField(TEXT("values"), Values) || !State->TryGetObjectField(TEXT("presets"), Presets))
    { Error = TEXT("Invalid environment state"); return false; }
    if (!EnvironmentRenderer->Apply(*Values, Error)) return false;
    Environment = LCParseJson(LCJsonString(State));
    return true;
}

TSharedPtr<FJsonObject> ULCTxService::EnvironmentRead(bool PresetsOnly) const
{
    auto Result = MakeShared<FJsonObject>();
    auto Presets = ULCEnvironment::Presets();
    for (const auto& Item : Environment->GetObjectField(TEXT("presets"))->Values) Presets->SetField(Item.Key, Item.Value);
    Result->SetObjectField(TEXT("presets"), Presets);
    Result->SetNumberField(TEXT("version"), Version);
    Result->SetBoolField(TEXT("busy"), IsBusy());
    if (!PresetsOnly)
    {
        Result->SetBoolField(TEXT("preview_active"), EnvironmentPreview.IsValid());
        Result->SetNumberField(TEXT("preview_version"), EnvironmentPreviewVersion);
        Result->SetObjectField(TEXT("state"), EnvironmentPreview ? EnvironmentPreview : Environment->GetObjectField(TEXT("values")));
        Result->SetObjectField(TEXT("committed"), Environment->GetObjectField(TEXT("values")));
        Result->SetObjectField(TEXT("observed"), EnvironmentRenderer->Observe());
    }
    return Result;
}

bool ULCTxService::PlanEnvironment(const TSharedPtr<FJsonObject>& Request, FString& Error)
{
    FString Action; Request->TryGetStringField(TEXT("action"), Action);
    auto After = LCParseJson(LCJsonString(Environment));
    TSharedPtr<FJsonObject> Values = After->GetObjectField(TEXT("values"));
    if (Action == TEXT("commit"))
    {
        int64 Expected = -1;
        if (!EnvironmentPreview || !Request->TryGetNumberField(TEXT("expected_version"), Expected) || Expected != Version || Expected != EnvironmentPreviewVersion)
        { Error = TEXT("No preview or stale expected_version; read environment first"); return false; }
        Values = EnvironmentPreview;
    }
    else if (Action == TEXT("save"))
    {
        FString Name;
        auto Presets = After->GetObjectField(TEXT("presets"));
        if (EnvironmentPreview || !Request->TryGetStringField(TEXT("name"), Name) || !LCValidId(Name) || Presets->Values.Num() >= 128 ||
            Presets->HasField(Name) || ULCEnvironment::Presets()->HasField(Name))
        { Error = TEXT("Save requires a new kebab-case name, no preview, and fewer than 128 custom presets"); return false; }
        Presets->SetObjectField(Name, Values);
    }
    else if (Action == TEXT("apply") || Action == TEXT("preview"))
    {
        if (Action == TEXT("apply") && EnvironmentPreview) { Error = TEXT("Commit or cancel the existing preview first"); return false; }
        if (EnvironmentPreview) Values = EnvironmentPreview;
        FString Preset;
        if (Request->HasField(TEXT("preset")))
        {
            if (!Request->TryGetStringField(TEXT("preset"), Preset)) { Error = TEXT("preset must be a string"); return false; }
            const auto Presets = EnvironmentRead(true)->GetObjectField(TEXT("presets"));
            const TSharedPtr<FJsonObject>* Selected = nullptr;
            if (!Presets->TryGetObjectField(Preset, Selected)) { Error = TEXT("Unknown environment preset: ") + Preset; return false; }
            Values = *Selected;
        }
        const TSharedPtr<FJsonObject>* Changes = nullptr;
        if (Request->HasField(TEXT("values")))
        {
            if (!Request->TryGetObjectField(TEXT("values"), Changes)) { Error = TEXT("values must be an object"); return false; }
            TSharedPtr<FJsonObject> Patched;
            if (!ULCEnvironment::Patch(Values, *Changes, Patched, Error)) return false;
            Values = Patched;
        }
        if (Preset.IsEmpty() && (!Changes || (*Changes)->Values.IsEmpty())) { Error = TEXT("Specify a preset or nonempty values"); return false; }
    }
    else { Error = TEXT("Unknown environment action"); return false; }
    After->SetObjectField(TEXT("values"), Values);
    Pending.Command = TEXT("environment ") + Action;
    Pending.Changes.Add({TEXT("@environment"), Environment, After});
    return true;
}

TSharedPtr<FJsonObject> ULCTxService::EnvironmentControl(const TSharedPtr<FJsonObject>& Request)
{
    FString Id, Action, Error;
    if (!Request || !Request->TryGetStringField(TEXT("req_id"), Id) || Id.IsEmpty() || Id.Len() > 128)
        return MakeReceipt(TEXT(""), TEXT("rejected"), TEXT("req_id must contain 1..128 characters"));
    if (Receipts.Contains(Id)) return Receipts[Id];
    Request->TryGetStringField(TEXT("action"), Action);
    TSet<FString> Allowed = {TEXT("req_id"), TEXT("action"), TEXT("command")};
    if (Action == TEXT("apply") || Action == TEXT("preview")) { Allowed.Add(TEXT("preset")); Allowed.Add(TEXT("values")); }
    if (Action == TEXT("preview")) Allowed.Add(TEXT("rate_h_per_s"));
    if (Action == TEXT("commit")) Allowed.Add(TEXT("expected_version"));
    if (Action == TEXT("save")) Allowed.Add(TEXT("name"));
    for (const auto& Field : Request->Values) if (!Allowed.Contains(FString(Field.Key))) Error = TEXT("Unexpected environment field: ") + FString(Field.Key);
    if (IsBusy() || QueuedCommit || DesignPreview) Error = TEXT("Transaction service is busy");
    auto Status = Presentation->Status();
    const FString PresentationState = Status->GetStringField(TEXT("state"));
    if (PresentationState == TEXT("previewing") || PresentationState == TEXT("settled") || PresentationState == TEXT("committed"))
        Error = TEXT("Cancel or finish the construction presentation first");
    double Rate = 0;
    if (Request->HasField(TEXT("rate_h_per_s")) && (!Request->TryGetNumberField(TEXT("rate_h_per_s"), Rate) || !FMath::IsFinite(Rate) || Rate < 0 || Rate > 2))
        Error = TEXT("rate_h_per_s must be within 0..2");
    if (Error.IsEmpty() && (Action == TEXT("apply") || Action == TEXT("commit") || Action == TEXT("save")))
        return Submit(Request, false, TEXT("environment"));
    const double Start = FPlatformTime::Seconds();
    if (Error.IsEmpty() && Action == TEXT("preview"))
    {
        const FLCStoreEdit SavedPending = Pending;
        Pending = FLCStoreEdit();
        if (PlanEnvironment(Request, Error))
        {
            auto Values = Pending.Changes[0].After->GetObjectField(TEXT("values"));
            if (EnvironmentRenderer->Apply(Values, Error))
            { EnvironmentPreview = LCParseJson(LCJsonString(Values)); EnvironmentPreviewVersion = Version; EnvironmentRate = Rate; }
        }
        Pending = SavedPending;
    }
    else if (Error.IsEmpty() && Action == TEXT("cancel"))
    {
        if (EnvironmentRenderer->Apply(Environment->GetObjectField(TEXT("values")), Error))
        { EnvironmentPreview.Reset(); EnvironmentPreviewVersion = -1; EnvironmentRate = 0; }
    }
    else if (Error.IsEmpty()) Error = TEXT("Use apply, preview, commit, cancel or save");
    auto Result = MakeReceipt(Id, Error.IsEmpty() ? TEXT("applied") : TEXT("rejected"), Error);
    Result->SetStringField(TEXT("tx_id"), TEXT("environment-") + FGuid::NewGuid().ToString(EGuidFormats::Digits));
    Result->SetNumberField(TEXT("ms"), (FPlatformTime::Seconds() - Start) * 1000);
    Receipts.Add(Id, Result);
    return Result;
}
