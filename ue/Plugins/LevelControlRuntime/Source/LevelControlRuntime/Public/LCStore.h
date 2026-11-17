#pragma once
#include "CoreMinimal.h"
#include "LCTypes.h"
#include "SQLiteDatabase.h"

struct FLCChange
{
    FString Id;
    TSharedPtr<FJsonObject> Before;
    TSharedPtr<FJsonObject> After;
};

struct FLCStoreEdit
{
    FString RequestId;
    FString TransactionId;
    FString Command;
    FString Kind = TEXT("edit");
    FString UndoTarget;
    int64 Version = 0;
    TSharedPtr<FJsonObject> Receipt;
    TArray<FLCChange> Changes;
    FString CheckpointName;
    FString CheckpointSnapshot;
    int64 CheckpointVersion = -1;
};

class LEVELCONTROLRUNTIME_API FLCStore
{
public:
    bool Open(const FString& Path);
    bool Close();
    bool Load(TMap<FString, FLCEntity>& Entities, TMap<FString, TSharedPtr<FJsonObject>>& Receipts, int64& Version, TSharedPtr<FJsonObject>* Environment = nullptr, TSharedPtr<FJsonObject>* Semantic = nullptr);
    bool Commit(const FLCStoreEdit& Edit);
    bool LatestEdit(FLCStoreEdit& Edit);
    bool UpdateReceipt(const FString& RequestId, const TSharedPtr<FJsonObject>& Receipt);
    bool Checkpoint(const FString& Name, TMap<FString, FLCEntity>& Entities, TSharedPtr<FJsonObject>* Environment = nullptr, TSharedPtr<FJsonObject>* Semantic = nullptr);
    TSharedPtr<FJsonObject> Checkpoints();
    FString Error;
    bool SpatialCandidates(const FVector& Min, const FVector& Max, TSet<FString>& Ids);
    TSharedPtr<FJsonObject> Changes(int64 Since, int64 Through);

private:
    bool Execute(const TCHAR* Sql, const TArray<FString>& Values = {});
    FSQLiteDatabase Database;
    FCriticalSection Mutex;
    bool NeedsIndexRebuild = false;
    bool IndexIdentity(const FString& Id, const TSharedPtr<FJsonObject>& Value);
    bool IndexEntity(const FString& Id, const TSharedPtr<FJsonObject>& Value);
};
