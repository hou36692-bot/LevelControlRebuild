#include "LCStore.h"
#include "HAL/FileManager.h"
#include "Misc/Paths.h"
#include "Misc/ScopeExit.h"
#include "Misc/ScopeLock.h"
#include "LCSemantic.h"

bool FLCStore::Execute(const TCHAR* Sql, const TArray<FString>& Values)
{
    auto Statement = Database.PrepareStatement(Sql);
    bool Success = Statement.IsValid();
    for (int32 Index = 0; Success && Index < Values.Num(); ++Index)
        Success = Statement.SetBindingValueByIndex(Index + 1, Values[Index]);
    if (Success) Success = Statement.Step() == ESQLitePreparedStatementStepResult::Done;
    if (!Success) Error = Database.GetLastError();
    return Success;
}

bool FLCStore::Open(const FString& Path)
{
    FScopeLock Lock(&Mutex);
    if (Path != TEXT(":memory:")) IFileManager::Get().MakeDirectory(*FPaths::GetPath(Path), true);
    const FString Copy = Path + TEXT(".semantic-v3.sqlite3");
    FString Effective = Path != TEXT(":memory:") && IFileManager::Get().FileExists(*Copy) ? Copy : Path;
    if (!Database.Open(*Effective)) { Error = Database.GetLastError(); return false; }
    int32 Schema = 0;
    if (!Database.GetUserVersion(Schema) || Schema < 0 || Schema > 3)
    { Error = TEXT("Unsupported database schema version"); return false; }
    if (Schema > 0 && Schema < 3 && Path != TEXT(":memory:") && Effective == Path)
    {
        if (!Execute(TEXT("VACUUM INTO ?"), {Copy})) return false;
        Database.Close();
        if (!Database.Open(*Copy)) { Error = Database.GetLastError(); return false; }
    }
    // SQLiteCore executes one prepared statement per call.
    const TCHAR* Statements[] = {
        TEXT("PRAGMA foreign_keys=ON"), TEXT("PRAGMA journal_mode=WAL"),
        TEXT("PRAGMA synchronous=FULL"), TEXT("PRAGMA locking_mode=EXCLUSIVE"),
        TEXT("CREATE TABLE IF NOT EXISTS templates(id TEXT PRIMARY KEY, payload TEXT NOT NULL)"),
        TEXT("CREATE TABLE IF NOT EXISTS entities(id TEXT PRIMARY KEY, template TEXT NOT NULL REFERENCES templates(id), version INTEGER NOT NULL, payload TEXT NOT NULL)"),
        TEXT("CREATE TABLE IF NOT EXISTS edits(req_id TEXT PRIMARY KEY, tx_id TEXT UNIQUE NOT NULL, kind TEXT NOT NULL, command TEXT NOT NULL, version INTEGER NOT NULL, undone INTEGER NOT NULL DEFAULT 0, before_json TEXT NOT NULL, after_json TEXT NOT NULL, receipt TEXT NOT NULL)"),
        TEXT("CREATE TABLE IF NOT EXISTS checkpoints(name TEXT PRIMARY KEY, version INTEGER NOT NULL, payload TEXT NOT NULL)"),
        TEXT("CREATE TABLE IF NOT EXISTS world_state(id TEXT PRIMARY KEY, payload TEXT NOT NULL)"),
        TEXT("CREATE TABLE IF NOT EXISTS identities(id TEXT PRIMARY KEY, role TEXT NOT NULL, parent TEXT NOT NULL, source TEXT NOT NULL, payload TEXT NOT NULL)"),
        TEXT("CREATE INDEX IF NOT EXISTS identity_role ON identities(role)"),
        TEXT("CREATE INDEX IF NOT EXISTS identity_parent ON identities(parent)"),
        TEXT("CREATE INDEX IF NOT EXISTS identity_source ON identities(source)"),
        TEXT("CREATE TABLE IF NOT EXISTS identity_aliases(alias TEXT NOT NULL, id TEXT NOT NULL REFERENCES identities(id) ON DELETE CASCADE, PRIMARY KEY(alias,id))"),
        TEXT("CREATE TABLE IF NOT EXISTS identity_relations(id TEXT NOT NULL REFERENCES identities(id) ON DELETE CASCADE, kind TEXT NOT NULL, target TEXT NOT NULL, PRIMARY KEY(id,kind,target))"),
        TEXT("CREATE INDEX IF NOT EXISTS relation_target ON identity_relations(kind,target)"),
        TEXT("CREATE TABLE IF NOT EXISTS spatial_ids(row_id INTEGER PRIMARY KEY, entity_id TEXT UNIQUE NOT NULL)"),
        TEXT("CREATE VIRTUAL TABLE IF NOT EXISTS entity_bounds USING rtree(row_id,min_x,max_x,min_y,max_y,min_z,max_z)"),
        TEXT("CREATE INDEX IF NOT EXISTS edit_version ON edits(version)"),
        TEXT("PRAGMA user_version=3")
    };
    for (const TCHAR* Statement : Statements)
        if (!Database.Execute(Statement)) { Error = Database.GetLastError(); return false; }
    auto Counts = Database.PrepareStatement(TEXT("SELECT (SELECT count(*) FROM entities)!=(SELECT count(*) FROM entity_bounds)"));
    if (!Counts.IsValid() || Counts.Step() != ESQLitePreparedStatementStepResult::Row) { Error = Database.GetLastError(); return false; }
    int32 Missing = 0; Counts.GetColumnValueByIndex(0, Missing); NeedsIndexRebuild = Missing != 0 || Schema < 3;
    for (const auto& Template : LCTemplates())
    {
        auto Json = MakeShared<FJsonObject>();
        Json->SetStringField(TEXT("id"), Template.Id);
        Json->SetArrayField(TEXT("size_cm"), LCVector(Template.Size));
        if (!Execute(TEXT("INSERT OR REPLACE INTO templates VALUES(?,?)"), {Template.Id, LCJsonString(Json)})) return false;
    }
    return true;
}

bool FLCStore::Close() { FScopeLock Lock(&Mutex); return Database.Close(); }

bool FLCStore::Load(TMap<FString, FLCEntity>& Entities, TMap<FString, TSharedPtr<FJsonObject>>& Receipts, int64& Version, TSharedPtr<FJsonObject>* Environment, TSharedPtr<FJsonObject>* Semantic)
{
    FScopeLock Lock(&Mutex);
    Entities.Reset();
    Receipts.Reset();
    Version = 0;
    if (Semantic)
    {
        *Semantic = LCEmptySemanticState();
        auto Row = Database.PrepareStatement(TEXT("SELECT payload FROM world_state WHERE id='@semantics'"));
        if (!Row.IsValid()) { Error = Database.GetLastError(); return false; }
        const auto State = Row.Step();
        if (State == ESQLitePreparedStatementStepResult::Row)
        { FString Payload; Row.GetColumnValueByIndex(0, Payload); *Semantic = LCParseJson(Payload); if (!*Semantic) { Error = TEXT("Invalid semantic state"); return false; } }
        else if (State != ESQLitePreparedStatementStepResult::Done) { Error = Database.GetLastError(); return false; }
    }
    if (Environment)
    {
        Environment->Reset();
        auto Row = Database.PrepareStatement(TEXT("SELECT payload FROM world_state WHERE id='@environment'"));
        if (!Row.IsValid()) { Error = Database.GetLastError(); return false; }
        const auto State = Row.Step();
        if (State == ESQLitePreparedStatementStepResult::Row)
        {
            FString Payload; Row.GetColumnValueByIndex(0, Payload);
            *Environment = LCParseJson(Payload);
            if (!*Environment) { Error = TEXT("Invalid stored environment"); return false; }
        }
        else if (State != ESQLitePreparedStatementStepResult::Done) { Error = Database.GetLastError(); return false; }
    }
    auto EntityRows = Database.PrepareStatement(TEXT("SELECT payload FROM entities ORDER BY id"));
    if (!EntityRows.IsValid()) { Error = Database.GetLastError(); return false; }
    ESQLitePreparedStatementStepResult Step;
    while ((Step = EntityRows.Step()) == ESQLitePreparedStatementStepResult::Row)
    {
        FString Payload;
        EntityRows.GetColumnValueByIndex(0, Payload);
        FLCEntity Entity;
        if (!FLCEntity::FromJson(LCParseJson(Payload), Entity, Error)) return false;
        Entities.Add(Entity.Id, Entity);
    }
    if (Step != ESQLitePreparedStatementStepResult::Done) { Error = Database.GetLastError(); return false; }
    if (NeedsIndexRebuild)
    {
        if (!Execute(TEXT("BEGIN IMMEDIATE"))) return false;
        bool Success = true;
        for (const auto& Pair : Entities) if (!IndexEntity(Pair.Key, Pair.Value.ToJson())) { Success = false; break; }
        if (!Success || !Execute(TEXT("COMMIT"))) { Database.Execute(TEXT("ROLLBACK")); return false; }
        NeedsIndexRebuild = false;
    }
    auto EditRows = Database.PrepareStatement(TEXT("SELECT req_id,receipt,version FROM edits"));
    if (!EditRows.IsValid()) { Error = Database.GetLastError(); return false; }
    while ((Step = EditRows.Step()) == ESQLitePreparedStatementStepResult::Row)
    {
        FString Id, Payload;
        int64 RowVersion;
        EditRows.GetColumnValueByIndex(0, Id);
        EditRows.GetColumnValueByIndex(1, Payload);
        EditRows.GetColumnValueByIndex(2, RowVersion);
        auto Receipt = LCParseJson(Payload);
        if (!Receipt) { Error = TEXT("Invalid persisted receipt"); return false; }
        Receipts.Add(Id, Receipt);
        Version = FMath::Max(Version, RowVersion);
    }
    if (Step != ESQLitePreparedStatementStepResult::Done) { Error = Database.GetLastError(); return false; }
    return true;
}

bool FLCStore::Commit(const FLCStoreEdit& Edit)
{
    FScopeLock Lock(&Mutex);
    if (!Execute(TEXT("BEGIN IMMEDIATE"))) return false;
    bool Done = false;
    ON_SCOPE_EXIT { if (!Done) Database.Execute(TEXT("ROLLBACK")); };
    auto Before = MakeShared<FJsonObject>();
    auto After = MakeShared<FJsonObject>();
    for (const auto& Change : Edit.Changes)
    {
        if (Change.Before) Before->SetObjectField(Change.Id, Change.Before);
        else Before->SetField(Change.Id, MakeShared<FJsonValueNull>());
        if (Change.After)
        {
            After->SetObjectField(Change.Id, Change.After);
            if (Change.Id.StartsWith(TEXT("@")))
            {
                if (!Execute(TEXT("INSERT OR REPLACE INTO world_state VALUES(?,?)"), {Change.Id, LCJsonString(Change.After)})) return false;
            }
            else if (!Execute(TEXT("INSERT OR REPLACE INTO entities VALUES(?,?,?,?)"),
                {Change.Id, Change.After->GetStringField(TEXT("template")), LexToString(Edit.Version), LCJsonString(Change.After)})) return false;
        }
        else
        {
            After->SetField(Change.Id, MakeShared<FJsonValueNull>());
            if (!Execute(Change.Id.StartsWith(TEXT("@")) ? TEXT("DELETE FROM world_state WHERE id=?") : TEXT("DELETE FROM entities WHERE id=?"), {Change.Id})) return false;
        }
        if (!Change.Id.StartsWith(TEXT("@")) && !IndexEntity(Change.Id, Change.After)) return false;
        if (Change.Id == TEXT("@semantics"))
        {
            if (!Execute(TEXT("DELETE FROM identities WHERE id NOT IN (SELECT id FROM entities)"))) return false;
            if (Change.After) for (const auto& Node : Change.After->GetObjectField(TEXT("nodes"))->Values)
                if (!IndexIdentity(FString(Node.Key), Node.Value->AsObject())) return false;
        }
    }
    if (!Edit.CheckpointName.IsEmpty() && !Execute(TEXT("INSERT INTO checkpoints VALUES(?,?,?)"),
        {Edit.CheckpointName, LexToString(Edit.CheckpointVersion >= 0 ? Edit.CheckpointVersion : Edit.Version - 1), Edit.CheckpointSnapshot})) return false;
    if (!Edit.UndoTarget.IsEmpty() && !Execute(TEXT("UPDATE edits SET undone=1 WHERE tx_id=?"), {Edit.UndoTarget})) return false;
    if (!Execute(TEXT("INSERT INTO edits(req_id,tx_id,kind,command,version,before_json,after_json,receipt) VALUES(?,?,?,?,?,?,?,?)"),
        {Edit.RequestId, Edit.TransactionId, Edit.Kind, Edit.Command, LexToString(Edit.Version), LCJsonString(Before), LCJsonString(After), LCJsonString(Edit.Receipt)})) return false;
    Done = Execute(TEXT("COMMIT"));
    return Done;
}

bool FLCStore::LatestEdit(FLCStoreEdit& Edit)
{
    FScopeLock Lock(&Mutex);
    auto Row = Database.PrepareStatement(TEXT("SELECT req_id,tx_id,command,version,before_json,after_json FROM edits WHERE kind='edit' AND undone=0 ORDER BY version DESC LIMIT 1"));
    if (!Row.IsValid()) { Error = Database.GetLastError(); return false; }
    if (Row.Step() != ESQLitePreparedStatementStepResult::Row) return false;
    Edit.Changes.Reset();
        FString BeforeText, AfterText;
        Row.GetColumnValueByIndex(0, Edit.RequestId);
        Row.GetColumnValueByIndex(1, Edit.TransactionId);
        Row.GetColumnValueByIndex(2, Edit.Command);
        Row.GetColumnValueByIndex(3, Edit.Version);
        Row.GetColumnValueByIndex(4, BeforeText);
        Row.GetColumnValueByIndex(5, AfterText);
        auto Before = LCParseJson(BeforeText), After = LCParseJson(AfterText);
        if (!Before || !After) { Error = TEXT("Corrupt undo history"); return false; }
        for (const auto& Field : Before->Values)
        {
            const auto Value = After->TryGetField(Field.Key);
            if (!Value) { Error = TEXT("Corrupt undo history"); return false; }
            Edit.Changes.Add({FString(Field.Key), Field.Value->IsNull() ? nullptr : Field.Value->AsObject(), Value->IsNull() ? nullptr : Value->AsObject()});
        }
    return true;
}

bool FLCStore::UpdateReceipt(const FString& RequestId, const TSharedPtr<FJsonObject>& Receipt)
{
    FScopeLock Lock(&Mutex);
    return Execute(TEXT("UPDATE edits SET receipt=? WHERE req_id=?"), {LCJsonString(Receipt), RequestId});
}

bool FLCStore::Checkpoint(const FString& Name, TMap<FString, FLCEntity>& Entities, TSharedPtr<FJsonObject>* Environment, TSharedPtr<FJsonObject>* Semantic)
{
    FScopeLock Lock(&Mutex);
    auto Row = Database.PrepareStatement(TEXT("SELECT payload FROM checkpoints WHERE name=?"));
    if (!Row.IsValid() || !Row.SetBindingValueByIndex(1, Name)) { Error = Database.GetLastError(); return false; }
    if (Row.Step() != ESQLitePreparedStatementStepResult::Row) { Error = TEXT("Checkpoint not found"); return false; }
    FString Payload; Row.GetColumnValueByIndex(0, Payload);
    auto Snapshot = LCParseJson(Payload);
    if (!Snapshot) { Error = TEXT("Invalid checkpoint snapshot"); return false; }
    Entities.Reset();
    if (Environment) Environment->Reset();
    if (Semantic) *Semantic = LCEmptySemanticState();
    for (const auto& Item : Snapshot->Values)
    {
        if (FString(Item.Key) == TEXT("@semantics"))
        { if (Item.Value->Type != EJson::Object) { Error = TEXT("Corrupt semantic snapshot"); return false; } if (Semantic) *Semantic = Item.Value->AsObject(); continue; }
        if (FString(Item.Key) == TEXT("@environment"))
        {
            if (Item.Value->Type != EJson::Object) { Error = TEXT("Corrupt checkpoint environment"); return false; }
            if (Environment) *Environment = Item.Value->AsObject();
            continue;
        }
        FLCEntity Entity;
        if (Item.Value->Type != EJson::Object || !FLCEntity::FromJson(Item.Value->AsObject(), Entity, Error) ||
            Entity.Id != FString(Item.Key)) { Error = TEXT("Corrupt checkpoint entity"); return false; }
        Entities.Add(Entity.Id, Entity);
    }
    return true;
}

TSharedPtr<FJsonObject> FLCStore::Checkpoints()
{
    FScopeLock Lock(&Mutex);
    auto Result = MakeShared<FJsonObject>(); TArray<TSharedPtr<FJsonValue>> Values;
    auto Rows = Database.PrepareStatement(TEXT("SELECT name,version FROM checkpoints ORDER BY name"));
    if (!Rows.IsValid()) { Result->SetStringField(TEXT("error"), Database.GetLastError()); return Result; }
    ESQLitePreparedStatementStepResult Step;
    while ((Step = Rows.Step()) == ESQLitePreparedStatementStepResult::Row)
    {
        FString Name; int64 Version; Rows.GetColumnValueByIndex(0, Name); Rows.GetColumnValueByIndex(1, Version);
        auto Value = MakeShared<FJsonObject>(); Value->SetStringField(TEXT("name"), Name); Value->SetNumberField(TEXT("version"), Version);
        Values.Add(MakeShared<FJsonValueObject>(Value));
    }
    if (Step != ESQLitePreparedStatementStepResult::Done) Result->SetStringField(TEXT("error"), Database.GetLastError());
    Result->SetArrayField(TEXT("checkpoints"), Values); return Result;
}
