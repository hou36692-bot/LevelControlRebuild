#pragma once
#include "LCTypes.h"
#include "InstancedStaticMeshDelegates.h"

class UWorld;
class AActor;
class ULevel;
class FLCStore;

LEVELCONTROLRUNTIME_API bool LCValidateSemantic(const TSharedPtr<FJsonObject>& Value, FString& Error, bool Public = false);
LEVELCONTROLRUNTIME_API TSharedPtr<FJsonObject> LCEmptySemanticState();

struct FLCSemanticBinding
{
    TWeakObjectPtr<AActor> Actor;
    TWeakObjectPtr<UInstancedStaticMeshComponent> Instances;
    int32 Index = INDEX_NONE;
};

struct FLCSemanticSelection
{
    int64 Version = 0;
    double Expires = 0;
    TArray<FString> Ids;
    TMap<FString, FString> Fingerprints;
};

/** Read model of committed identities and separate, sampled runtime observations. */
class LEVELCONTROLRUNTIME_API FLCSemanticWorld
{
public:
    void Initialize(UWorld* InWorld, AActor* InternalHost);
    void Shutdown();
    void Publish(const TMap<FString, FLCEntity>& Entities, const TSharedPtr<FJsonObject>& State, int64 Version);
    void Observe(const FString& Id, const TSharedPtr<FJsonObject>& Transform, const FString& Lifecycle);
    void Tick();
    TSharedPtr<FJsonObject> Query(const TSharedPtr<FJsonObject>& Request, FLCStore* Store = nullptr);
    TSharedPtr<FJsonObject> Changes(const TSharedPtr<FJsonObject>& Request, FLCStore& Store);
    bool ResolveSelection(const FString& Token, int64 Version, TArray<FString>& Ids, FString& Error);
    bool Expand(const FString& Id, TArray<FString>& Ids, FString& Error) const;
    TMap<FString, TSharedPtr<FJsonObject>> Rows;
    TMap<FString, TSharedPtr<FJsonObject>> Live;
    TSet<FString> Physical;
    FString Session;
    int64 CommittedVersion = 0;
private:
    void Register(AActor* Actor);
    void Unregister(AActor* Actor);
    void RemoveActor(AActor* Actor, const FString& Reason);
    void ApplySavedAnnotation(const FString& Id);
    void LevelAdded(ULevel* Level, UWorld* InWorld);
    void LevelRemoved(ULevel* Level, UWorld* InWorld);
    void InstanceChanged(UInstancedStaticMeshComponent* Component, TArrayView<const FInstancedStaticMeshDelegates::FInstanceIndexUpdateData> Updates);
    void RegisterInstance(UInstancedStaticMeshComponent* Component, int32 Index, bool Authored = false);
    void Sample(const FString& Id);
    void Reindex();
    void Event(const FString& Id, const FString& Kind, const TSharedPtr<FJsonObject>& State = nullptr);
    bool Descendant(const FString& Id, const FString& Parent) const;
    TSharedPtr<FJsonObject> Frame() const;
    UWorld* World = nullptr;
    TWeakObjectPtr<AActor> Host;
    TMap<FString, FLCSemanticBinding> Bindings;
    TMap<FString, TSharedPtr<FJsonObject>> External;
    TSharedPtr<FJsonObject> SavedNodes;
    TMap<FString, TSet<FString>> Index;
    TMap<FString, FLCSemanticSelection> Selections;
    TMap<FString, TSharedPtr<FJsonObject>> Pages;
    TArray<TSharedPtr<FJsonValue>> LiveEvents;
    TArray<TWeakObjectPtr<AActor>> PendingActors;
    TArray<FString> SampleIds;
    int32 SampleCursor = 0;
    int64 Sequence = 0;
    int64 RegistryRevision = 0;
    double NextSample = 0;
    FDelegateHandle SpawnHandle, DestroyHandle, AddedHandle, RemovedHandle, InstanceHandle;
};
