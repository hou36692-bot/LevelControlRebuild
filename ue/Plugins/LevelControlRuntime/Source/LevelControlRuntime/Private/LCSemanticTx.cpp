#include "LCTxService.h"
#include "LCSelector.h"

static bool SameValue(const TSharedPtr<FJsonValue>& A, const TSharedPtr<FJsonValue>& B)
{
    if (!A || A->IsNull()) return !B || B->IsNull();
    if (!B || B->IsNull()) return false;
    return FJsonValue::CompareEqual(*A, *B);
}

TSharedPtr<FJsonObject> ULCTxService::SemanticQuery(const TSharedPtr<FJsonObject>& Request)
{
    // A running transaction may have changed the durable index before publication.
    // Use the previous immutable in-memory snapshot until its receipt is published.
    return SemanticWorld.Query(Request, IsBusy() ? nullptr : &Store);
}

TSharedPtr<FJsonObject> ULCTxService::SemanticChanges(const TSharedPtr<FJsonObject>& Request)
{ return SemanticWorld.Changes(Request, Store); }

TSharedPtr<FJsonObject> ULCTxService::SemanticRead(const FString& Source) const
{
    TSharedPtr<FJsonObject> Result = MakeShared<FJsonObject>(); Result->SetNumberField(TEXT("version"), Version);
    TSharedPtr<FJsonObject> Overrides = MakeShared<FJsonObject>();
    for (const auto& Pair : CommittedSemanticState->GetObjectField(TEXT("overrides"))->Values)
    {
        FString Owner; Pair.Value->AsObject()->TryGetStringField(TEXT("source"), Owner);
        if (Source.IsEmpty() || Owner == Source) Overrides->SetField(FString(Pair.Key), Pair.Value);
    }
    Result->SetObjectField(TEXT("overrides"), Overrides);
    Result->SetStringField(TEXT("session_id"), SemanticWorld.Session);
    return Result;
}

bool ULCTxService::ResolveEditTargets(const FString& Selector, TArray<FString>& Ids, FString& Error)
{
    if (Selector == TEXT("selected"))
    {
        if (LockedSelection.IsEmpty()) { Error = TEXT("selected requires a current selection_id"); return false; }
        Ids = LockedSelection; return true;
    }
    if (!LockedSelection.IsEmpty()) { Error = TEXT("A selection-bound edit must use selected and cannot add other targets"); return false; }
    if (SemanticWorld.Rows.Contains(Selector) && !Entities.Contains(Selector)) return SemanticWorld.Expand(Selector, Ids, Error);
    return LCResolve(Selector, Entities, FVector::ZeroVector, false, false, Ids, Error);
}

bool ULCTxService::ApplyInstanceOverride(FLCEntity& Entity, FString& Error) const
{
    const TSharedPtr<FJsonObject>* Override = nullptr;
    if (!SemanticState->GetObjectField(TEXT("overrides"))->TryGetObjectField(Entity.Id, Override)) return true;
    const TSharedPtr<FJsonObject>* Fields = nullptr;
    if (!(*Override)->TryGetObjectField(TEXT("fields"), Fields)) return true;
    auto Value = Entity.ToJson();
    for (const auto& Pair : (*Fields)->Values)
    {
        FString Parent, Key; auto Target = Value;
        if (FString(Pair.Key).Split(TEXT("."), &Parent, &Key))
        { const TSharedPtr<FJsonObject>* Object = nullptr; if (!Target->TryGetObjectField(Parent, Object)) { Error = TEXT("Override field is missing: ") + FString(Pair.Key); return false; } Target = *Object; }
        else Key = FString(Pair.Key);
        auto Patch = Pair.Value->AsObject(); auto Current = Target->TryGetField(Key);
        if (!SameValue(Current, Patch->TryGetField(TEXT("base"))) && !SameValue(Current, Patch->TryGetField(TEXT("value"))))
        { Error = TEXT("Instance override conflict: ") + Entity.Id + TEXT(" ") + FString(Pair.Key); return false; }
        auto After = Patch->TryGetField(TEXT("value")); if (After && !After->IsNull()) Target->SetField(Key, After); else Target->RemoveField(Key);
    }
    return FLCEntity::FromJson(Value, Entity, Error);
}

bool ULCTxService::PlanAnnotations(const TSharedPtr<FJsonObject>& Request, FString& Error)
{
    TArray<FString> Ids; const TSharedPtr<FJsonObject>* Patch = nullptr;
    if (!Request->HasField(TEXT("expected_version")) || !Request->TryGetStringArrayField(TEXT("ids"), Ids) || Ids.IsEmpty() || Ids.Num() > 256 ||
        !Request->TryGetObjectField(TEXT("semantic"), Patch) || !LCValidateSemantic(*Patch, Error, true))
    { if (Error.IsEmpty()) Error = TEXT("annotations require ids, semantic and expected_version"); return false; }
    auto Next = LCParseJson(LCJsonString(SemanticState));
    if (!Next->HasField(TEXT("annotations"))) Next->SetObjectField(TEXT("annotations"), MakeShared<FJsonObject>());
    auto Nodes = Next->GetObjectField(TEXT("nodes"));
    TSet<FString> Seen;
    for (const auto& Id : Ids)
    {
        if (!SemanticWorld.Rows.Contains(Id) || Seen.Contains(Id)) { Error = TEXT("Unknown or duplicate annotation identity: ") + Id; return false; }
        Seen.Add(Id);
        if (const auto* Entity = Entities.Find(Id))
        {
            auto After = Entity->ToJson(); auto Metadata = Entity->Semantic ? LCParseJson(LCJsonString(Entity->Semantic)) : MakeShared<FJsonObject>();
            for (const auto& Pair : (*Patch)->Values) Metadata->SetField(FString(Pair.Key), Pair.Value);
            if (!(*Patch)->HasField(TEXT("provenance"))) Metadata->SetStringField(TEXT("provenance"), TEXT("manual"));
            After->SetObjectField(TEXT("semantic"), Metadata); After->SetNumberField(TEXT("version"), Pending.Version);
            Pending.Changes.Add({Id, Entity->ToJson(), After});
        }
        else
        {
            TSharedPtr<FJsonObject> Metadata = MakeShared<FJsonObject>(); const auto Current = SemanticWorld.Rows[Id];
            const TSharedPtr<FJsonObject>* Old = nullptr;
            if (Nodes->TryGetObjectField(Id, Old)) Metadata = LCParseJson(LCJsonString(*Old));
            Metadata->SetStringField(TEXT("id"), Id); Metadata->SetStringField(TEXT("source"), Current->GetStringField(TEXT("source")));
            for (const auto& Pair : (*Patch)->Values) Metadata->SetField(FString(Pair.Key), Pair.Value);
            if (!(*Patch)->HasField(TEXT("provenance"))) Metadata->SetStringField(TEXT("provenance"), TEXT("manual"));
            Nodes->SetObjectField(Id, Metadata);
            TSharedPtr<FJsonObject> Annotation = MakeShared<FJsonObject>();
            if (Next->GetObjectField(TEXT("annotations"))->TryGetObjectField(Id, Old)) Annotation = LCParseJson(LCJsonString(*Old));
            for (const auto& Pair : (*Patch)->Values) Annotation->SetField(FString(Pair.Key), Pair.Value);
            Annotation->SetStringField(TEXT("provenance"), Metadata->GetStringField(TEXT("provenance")));
            Next->GetObjectField(TEXT("annotations"))->SetObjectField(Id, Annotation);
        }
    }
    Pending.Command = TEXT("annotate identities");
    Pending.Changes.Add({TEXT("@semantics"), SemanticState, Next});
    return true;
}

bool ULCTxService::PlanSemanticState(const TSharedPtr<FJsonObject>& Request, const FString& Mode, bool Undo, FString& Error)
{
    if (Undo || Mode == TEXT("checkpoint")) return true;
    auto Next = LCParseJson(LCJsonString(SemanticState)); int32 SemanticIndex = INDEX_NONE;
    for (int32 I = 0; I < Pending.Changes.Num(); ++I) if (Pending.Changes[I].Id == TEXT("@semantics")) { Next = Pending.Changes[I].After; SemanticIndex = I; }
    auto Overrides = Next->GetObjectField(TEXT("overrides")); auto Nodes = Next->GetObjectField(TEXT("nodes"));
    if (Mode == TEXT("reconcile"))
    {
        const FString Source = Request->GetStringField(TEXT("source")); FString Policy;
        Request->TryGetStringField(TEXT("override_policy"), Policy);
        if (!Policy.IsEmpty() && Policy != TEXT("preserve") && Policy != TEXT("discard")) { Error = TEXT("Unknown override_policy"); return false; }
        if (Policy == TEXT("discard"))
        {
            TArray<FString> Remove;
            for (const auto& Pair : Overrides->Values) if (Pair.Value->AsObject()->GetStringField(TEXT("source")) == Source) Remove.Add(FString(Pair.Key));
            for (const auto& Id : Remove) Overrides->RemoveField(Id);
        }
        const TSharedPtr<FJsonObject>* Incoming = nullptr;
        if (Request->HasField(TEXT("semantic_nodes")))
        {
            if (!Request->TryGetObjectField(TEXT("semantic_nodes"), Incoming) || (*Incoming)->Values.Num() > 32768) { Error = TEXT("Invalid semantic_nodes"); return false; }
            TArray<FString> Remove;
            for (const auto& Pair : Nodes->Values) if (Pair.Value->AsObject()->GetStringField(TEXT("source")) == Source) Remove.Add(FString(Pair.Key));
            for (const auto& Id : Remove) Nodes->RemoveField(Id);
            for (const auto& Pair : (*Incoming)->Values)
            {
                if (!LCValidId(FString(Pair.Key)) || Pair.Value->Type != EJson::Object || !LCValidateSemantic(Pair.Value->AsObject(), Error)) return false;
                auto Node = LCParseJson(LCJsonString(Pair.Value->AsObject())); FString Owner;
                if (!Node->TryGetStringField(TEXT("source"), Owner) || Owner != Source || Entities.Contains(FString(Pair.Key)) || Nodes->HasField(FString(Pair.Key)) ||
                    (SemanticWorld.Rows.Contains(FString(Pair.Key)) && SemanticWorld.Rows[FString(Pair.Key)]->GetStringField(TEXT("source")) != Source))
                { Error = TEXT("Logical identity ownership conflict: ") + FString(Pair.Key); return false; }
                Node->SetStringField(TEXT("id"), FString(Pair.Key));
                const TSharedPtr<FJsonObject>* Annotations = nullptr;
                const TSharedPtr<FJsonObject>* Annotation = nullptr;
                if (Next->TryGetObjectField(TEXT("annotations"), Annotations) && (*Annotations)->TryGetObjectField(FString(Pair.Key), Annotation))
                for (const auto& Field : (*Annotation)->Values) Node->SetField(FString(Field.Key), Field.Value);
                Nodes->SetObjectField(FString(Pair.Key), Node);
            }
        }
        const TArray<TSharedPtr<FJsonValue>>* Constraints;
        if (Request->HasField(TEXT("constraints")))
        {
            if (!Request->TryGetArrayField(TEXT("constraints"), Constraints) || Constraints->Num() > 256) { Error = TEXT("Invalid design constraints"); return false; }
            TSharedPtr<FJsonObject> Record = MakeShared<FJsonObject>(); Record->SetArrayField(TEXT("declared"), *Constraints); Record->SetStringField(TEXT("validation"), TEXT("not_checked"));
            Next->GetObjectField(TEXT("constraints"))->SetObjectField(Source, Record);
        }
    }
    else if (Mode.IsEmpty() || Mode == TEXT("action") || Mode == TEXT("annotations"))
    {
        for (const auto& Change : Pending.Changes)
        {
            if (Change.Id.StartsWith(TEXT("@"))) continue;
            const auto Value = Change.Before ? Change.Before : Change.After; FString Source;
            if (!Value || !Value->TryGetStringField(TEXT("source"), Source) || Source.IsEmpty()) continue;
            const TSharedPtr<FJsonObject>* Existing = nullptr; TSharedPtr<FJsonObject> Override = MakeShared<FJsonObject>();
            if (Overrides->TryGetObjectField(Change.Id, Existing)) Override = LCParseJson(LCJsonString(*Existing));
            Override->SetStringField(TEXT("source"), Source);
            if (!Change.After)
            {
                Override->SetBoolField(TEXT("deleted"), true);
                Override->RemoveField(TEXT("added"));
                Override->RemoveField(TEXT("entity"));
            }
            else if (!Change.Before || (Override->HasField(TEXT("added")) && Override->GetBoolField(TEXT("added"))))
            {
                Override->SetBoolField(TEXT("added"), true);
                Override->RemoveField(TEXT("deleted"));
                Override->SetObjectField(TEXT("entity"), Change.After);
            }
            else
            {
                Override->RemoveField(TEXT("deleted"));
                Override->RemoveField(TEXT("added"));
                Override->RemoveField(TEXT("entity"));
                TSharedPtr<FJsonObject> Fields = MakeShared<FJsonObject>(); if (Override->TryGetObjectField(TEXT("fields"), Existing)) Fields = *Existing;
                auto Record = [&](const FString& Key, TSharedPtr<FJsonValue> Before, TSharedPtr<FJsonValue> After)
                {
                    if (SameValue(Before, After)) return;
                    TSharedPtr<FJsonObject> Patch = MakeShared<FJsonObject>(); const TSharedPtr<FJsonObject>* Prior = nullptr;
                    Patch->SetField(TEXT("base"), Fields->TryGetObjectField(Key, Prior) ? (*Prior)->TryGetField(TEXT("base")) : Before ? Before : MakeShared<FJsonValueNull>());
                    Patch->SetField(TEXT("value"), After ? After : MakeShared<FJsonValueNull>());
                    if (SameValue(Patch->TryGetField(TEXT("base")), Patch->TryGetField(TEXT("value")))) Fields->RemoveField(Key);
                    else Fields->SetObjectField(Key, Patch);
                };
                for (auto Key : {TEXT("position_cm"), TEXT("rotation_deg"), TEXT("size_cm")}) Record(FString(TEXT("transform.")) + Key,
                    Change.Before->GetObjectField(TEXT("transform"))->TryGetField(Key), Change.After->GetObjectField(TEXT("transform"))->TryGetField(Key));
                for (auto Key : {TEXT("surface"), TEXT("collision"), TEXT("lod"), TEXT("template"), TEXT("semantic")}) Record(Key, Change.Before->TryGetField(Key), Change.After->TryGetField(Key));
                Override->SetObjectField(TEXT("fields"), Fields);
            }
            if (Override->HasField(TEXT("fields")) && Override->GetObjectField(TEXT("fields"))->Values.IsEmpty() && !Override->HasField(TEXT("added")) && !Override->HasField(TEXT("deleted"))) Overrides->RemoveField(Change.Id);
            else Overrides->SetObjectField(Change.Id, Override);
        }
    }
    TMap<FString, TSharedPtr<FJsonObject>> Geometry;
    for (const auto& Pair : Entities) Geometry.Add(FString(Pair.Key), Pair.Value.ToJson());
    for (const auto& Change : Pending.Changes) if (!Change.Id.StartsWith(TEXT("@"))) { if (Change.After) Geometry.Add(Change.Id, Change.After); else Geometry.Remove(Change.Id); }
    for (const auto& Node : Nodes->Values) if (Geometry.Contains(FString(Node.Key)))
    { Error = TEXT("Logical and physical identities must be distinct: ") + FString(Node.Key); return false; }
    if (Mode == TEXT("reconcile"))
    {
        const FString Source = Request->GetStringField(TEXT("source"));
        for (const auto& Pair : Overrides->Values)
        {
            auto Override = Pair.Value->AsObject(); bool Deleted = false, Added = false;
            Override->TryGetBoolField(TEXT("deleted"), Deleted); Override->TryGetBoolField(TEXT("added"), Added);
            if (Override->GetStringField(TEXT("source")) == Source && !Deleted && !Added && !Geometry.Contains(FString(Pair.Key)))
            { Error = TEXT("Instance override conflict: edited member removed from design: ") + FString(Pair.Key); return false; }
        }
    }
    TSet<FString> Used;
    for (const auto& Pair : Geometry)
    {
        const TSharedPtr<FJsonObject>* Semantic = nullptr; FString Parent;
        if (!Pair.Value->TryGetObjectField(TEXT("semantic"), Semantic) || !(*Semantic)->TryGetStringField(TEXT("parent"), Parent)) continue;
        TSet<FString> Seen;
        while (!Parent.IsEmpty())
        {
            if (Seen.Contains(Parent)) { Error = TEXT("Cyclic identity hierarchy"); return false; }
            Seen.Add(Parent); Used.Add(Parent);
            const TSharedPtr<FJsonObject>* Node = nullptr;
            if (!Nodes->TryGetObjectField(Parent, Node)) { Error = TEXT("Unknown logical parent: ") + Parent; return false; }
            if (!(*Node)->TryGetStringField(TEXT("parent"), Parent)) break;
        }
    }
    TArray<FString> Empty;
    for (const auto& Pair : Nodes->Values) if (Pair.Value->AsObject()->GetStringField(TEXT("source")) != TEXT("world") && !Used.Contains(FString(Pair.Key))) Empty.Add(FString(Pair.Key));
    for (const auto& Id : Empty) Nodes->RemoveField(Id);
    if (LCJsonString(Next) != LCJsonString(SemanticState))
    {
        if (SemanticIndex != INDEX_NONE) Pending.Changes[SemanticIndex].After = Next;
        else Pending.Changes.Add({TEXT("@semantics"), SemanticState, Next});
        if (Pending.Kind == TEXT("receipt")) { Pending.Kind = TEXT("edit"); Pending.Version = Version + 1; }
    }
    return true;
}
