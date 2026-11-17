#include "LCStore.h"
#include "Misc/ScopeLock.h"

static FString FieldText(const TSharedPtr<FJsonObject>& Value, const TCHAR* Key)
{ FString Result; Value->TryGetStringField(Key, Result); return Result; }

bool FLCStore::IndexIdentity(const FString& Id, const TSharedPtr<FJsonObject>& Value)
{
    if (!Execute(TEXT("INSERT INTO identities VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET role=excluded.role,parent=excluded.parent,source=excluded.source,payload=excluded.payload"),
        {Id, FieldText(Value, TEXT("role")), FieldText(Value, TEXT("parent")), FieldText(Value, TEXT("source")), LCJsonString(Value)})) return false;
    if (!Execute(TEXT("DELETE FROM identity_aliases WHERE id=?"), {Id}) || !Execute(TEXT("DELETE FROM identity_relations WHERE id=?"), {Id})) return false;
    TArray<FString> Aliases; Value->TryGetStringArrayField(TEXT("aliases"), Aliases);
    const auto Name = FieldText(Value, TEXT("name")); if (!Name.IsEmpty()) Aliases.AddUnique(Name);
    for (const auto& Alias : Aliases) if (!Execute(TEXT("INSERT OR IGNORE INTO identity_aliases VALUES(?,?)"), {Alias, Id})) return false;
    const TArray<TSharedPtr<FJsonValue>>* Relations;
    if (Value->TryGetArrayField(TEXT("relations"), Relations)) for (const auto& Relation : *Relations)
        if (!Execute(TEXT("INSERT OR IGNORE INTO identity_relations VALUES(?,?,?)"), {Id, FieldText(Relation->AsObject(), TEXT("kind")), FieldText(Relation->AsObject(), TEXT("target"))})) return false;
    return true;
}

bool FLCStore::IndexEntity(const FString& Id, const TSharedPtr<FJsonObject>& Value)
{
    if (!Execute(TEXT("DELETE FROM entity_bounds WHERE row_id=(SELECT row_id FROM spatial_ids WHERE entity_id=?)"), {Id})) return false;
    if (!Value)
        return Execute(TEXT("DELETE FROM spatial_ids WHERE entity_id=?"), {Id}) && Execute(TEXT("DELETE FROM identities WHERE id=?"), {Id});
    TSharedPtr<FJsonObject> Identity = MakeShared<FJsonObject>(); const TSharedPtr<FJsonObject>* Semantic = nullptr;
    if (Value->TryGetObjectField(TEXT("semantic"), Semantic)) Identity = LCParseJson(LCJsonString(*Semantic));
    Identity->SetStringField(TEXT("id"), Id); Identity->SetStringField(TEXT("source"), FieldText(Value, TEXT("source")));
    if (!IndexIdentity(Id, Identity)) return false;
    const TSharedPtr<FJsonObject>* Bounds = nullptr; FVector Min, Max;
    if (!Value->TryGetObjectField(TEXT("bounds"), Bounds) || !LCReadVector(*Bounds, TEXT("min_cm"), Min) || !LCReadVector(*Bounds, TEXT("max_cm"), Max))
    { Error = TEXT("Entity index requires bounds"); return false; }
    return Execute(TEXT("INSERT OR IGNORE INTO spatial_ids(entity_id) VALUES(?)"), {Id}) &&
        Execute(TEXT("INSERT INTO entity_bounds SELECT row_id,?,?,?,?,?,? FROM spatial_ids WHERE entity_id=?"),
            {LexToString(Min.X), LexToString(Max.X), LexToString(Min.Y), LexToString(Max.Y), LexToString(Min.Z), LexToString(Max.Z), Id});
}

bool FLCStore::SpatialCandidates(const FVector& Min, const FVector& Max, TSet<FString>& Ids)
{
    FScopeLock Lock(&Mutex);
    auto Rows = Database.PrepareStatement(TEXT("SELECT entity_id FROM spatial_ids JOIN entity_bounds USING(row_id) WHERE min_x<=? AND max_x>=? AND min_y<=? AND max_y>=? AND min_z<=? AND max_z>=?"));
    if (!Rows.IsValid()) { Error = Database.GetLastError(); return false; }
    const TArray<double> Values = {Max.X, Min.X, Max.Y, Min.Y, Max.Z, Min.Z};
    for (int32 I = 0; I < Values.Num(); ++I) if (!Rows.SetBindingValueByIndex(I + 1, Values[I])) { Error = Database.GetLastError(); return false; }
    ESQLitePreparedStatementStepResult Step;
    while ((Step = Rows.Step()) == ESQLitePreparedStatementStepResult::Row) { FString Id; Rows.GetColumnValueByIndex(0, Id); Ids.Add(Id); }
    if (Step != ESQLitePreparedStatementStepResult::Done) { Error = Database.GetLastError(); return false; }
    return true;
}

TSharedPtr<FJsonObject> FLCStore::Changes(int64 Since, int64 Through)
{
    FScopeLock Lock(&Mutex); TSharedPtr<FJsonObject> Result = MakeShared<FJsonObject>();
    auto Rows = Database.PrepareStatement(TEXT("SELECT version,tx_id,before_json,after_json FROM edits WHERE version>? AND version<=? AND kind IN ('edit','undo') ORDER BY version LIMIT 32"));
    if (!Rows.IsValid() || !Rows.SetBindingValueByIndex(1, Since) || !Rows.SetBindingValueByIndex(2, Through))
    { Result->SetStringField(TEXT("error"), Database.GetLastError()); return Result; }
    TArray<TSharedPtr<FJsonValue>> Changes; int64 Last = Since;
    ESQLitePreparedStatementStepResult Step;
    while ((Step = Rows.Step()) == ESQLitePreparedStatementStepResult::Row)
    {
        FString Transaction, BeforeText, AfterText; Rows.GetColumnValueByIndex(0, Last); Rows.GetColumnValueByIndex(1, Transaction);
        Rows.GetColumnValueByIndex(2, BeforeText); Rows.GetColumnValueByIndex(3, AfterText);
        const auto Before = LCParseJson(BeforeText), After = LCParseJson(AfterText);
        if (!Before || !After) { Result->SetStringField(TEXT("error"), TEXT("Corrupt change history")); return Result; }
        TSharedPtr<FJsonObject> Change = MakeShared<FJsonObject>(); Change->SetNumberField(TEXT("version"), Last); Change->SetStringField(TEXT("tx_id"), Transaction);
        TArray<TSharedPtr<FJsonValue>> Added, Updated, Deleted;
        TSet<FString> SemanticIds;
        for (const auto& Pair : After->Values)
        {
            if (FString(Pair.Key) == TEXT("@semantics"))
            {
                const auto OldValue = Before->TryGetField(FString(Pair.Key));
                if (OldValue && OldValue->Type == EJson::Object)
                    for (const auto& Node : OldValue->AsObject()->GetObjectField(TEXT("nodes"))->Values) SemanticIds.Add(FString(Node.Key));
                if (Pair.Value->Type == EJson::Object)
                    for (const auto& Node : Pair.Value->AsObject()->GetObjectField(TEXT("nodes"))->Values) SemanticIds.Add(FString(Node.Key));
                continue;
            }
            const auto Old = Before->TryGetField(FString(Pair.Key));
            (Pair.Value->IsNull() ? Deleted : !Old || Old->IsNull() ? Added : Updated).Add(MakeShared<FJsonValueString>(FString(Pair.Key)));
        }
        TArray<FString> Sorted = SemanticIds.Array(); Sorted.Sort(); TArray<TSharedPtr<FJsonValue>> Invalidated;
        for (const auto& Id : Sorted) Invalidated.Add(MakeShared<FJsonValueString>(Id));
        Change->SetArrayField(TEXT("added"), Added); Change->SetArrayField(TEXT("changed"), Updated); Change->SetArrayField(TEXT("deleted"), Deleted);
        Change->SetArrayField(TEXT("semantic_invalidated"), Invalidated); Changes.Add(MakeShared<FJsonValueObject>(Change));
    }
    if (Step != ESQLitePreparedStatementStepResult::Done) Result->SetStringField(TEXT("error"), Database.GetLastError());
    Result->SetArrayField(TEXT("durable"), Changes); Result->SetNumberField(TEXT("through_version"), Changes.Num() < 32 ? Through : Last);
    Result->SetBoolField(TEXT("has_more"), Changes.Num() == 32 && Last < Through);
    return Result;
}
