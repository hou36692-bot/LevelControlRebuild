#include "LCSemantic.h"
#include "LCIdentityComponent.h"
#include "LCStore.h"
#include "Engine/World.h"
#include "Engine/Level.h"
#include "Engine/StaticMesh.h"
#include "EngineUtils.h"
#include "Components/InstancedStaticMeshComponent.h"
#include "GameFramework/Actor.h"
#include "GameFramework/PlayerController.h"
#include "Camera/PlayerCameraManager.h"
#include "Kismet/GameplayStatics.h"

static TArray<TSharedPtr<FJsonValue>> Strings(std::initializer_list<const TCHAR*> Values)
{
    TArray<TSharedPtr<FJsonValue>> Result;
    for (auto Value : Values) Result.Add(MakeShared<FJsonValueString>(Value));
    return Result;
}

TSharedPtr<FJsonObject> LCEmptySemanticState()
{
    TSharedPtr<FJsonObject> Result = MakeShared<FJsonObject>();
    for (auto Key : {TEXT("nodes"), TEXT("overrides"), TEXT("constraints")}) Result->SetObjectField(Key, MakeShared<FJsonObject>());
    return Result;
}

bool LCValidateSemantic(const TSharedPtr<FJsonObject>& Value, FString& Error, bool Public)
{
    if (!Value) { Error = TEXT("semantic must be an object"); return false; }
    const TSet<FString> Names = {TEXT("name"), TEXT("role"), TEXT("area"), TEXT("pattern"), TEXT("ring"), TEXT("side"), TEXT("provenance"), TEXT("parent"), TEXT("member_key"), TEXT("identity_scope"), TEXT("source"), TEXT("id")};
    for (const auto& Field : Value->Values)
    {
        if (Public && (FString(Field.Key) == TEXT("id") || FString(Field.Key) == TEXT("source") || FString(Field.Key) == TEXT("identity_scope") || FString(Field.Key) == TEXT("member_key") || FString(Field.Key) == TEXT("parent")))
        { Error = TEXT("Annotation cannot change identity or ownership"); return false; }
        if (Names.Contains(FString(Field.Key)))
        {
            FString Text;
            if (!Field.Value->TryGetString(Text) || Text.Len() > 256) { Error = TEXT("Invalid semantic string: ") + FString(Field.Key); return false; }
        }
        else if (FString(Field.Key) == TEXT("aliases"))
        {
            TArray<FString> Aliases;
            if (!Value->TryGetStringArrayField(TEXT("aliases"), Aliases) || Aliases.Num() > 32) { Error = TEXT("Invalid aliases"); return false; }
            for (const auto& Alias : Aliases) if (Alias.IsEmpty() || Alias.Len() > 256) { Error = TEXT("Invalid alias length"); return false; }
        }
        else if (FString(Field.Key) == TEXT("relations"))
        {
            const TArray<TSharedPtr<FJsonValue>>* Relations;
            if (!Value->TryGetArrayField(TEXT("relations"), Relations) || Relations->Num() > 64) { Error = TEXT("Invalid relations"); return false; }
            for (const auto& Relation : *Relations)
            {
                if (Relation->Type != EJson::Object) { Error = TEXT("Relation must be an object"); return false; }
                FString Kind, Target;
                if (!Relation->AsObject()->TryGetStringField(TEXT("kind"), Kind) || Kind.IsEmpty() || Kind.Len() > 256 ||
                    !Relation->AsObject()->TryGetStringField(TEXT("target"), Target) || Target.IsEmpty() || Target.Len() > 256 || Relation->AsObject()->Values.Num() != 2)
                { Error = TEXT("Relation requires kind and target"); return false; }
            }
        }
        else if (FString(Field.Key) == TEXT("local_frame"))
        {
            if (Public || Field.Value->Type != EJson::Object) { Error = TEXT("Invalid local_frame"); return false; }
            FVector Point; double Yaw;
            if (!LCReadVector(Field.Value->AsObject(), TEXT("position_cm"), Point) || !Field.Value->AsObject()->TryGetNumberField(TEXT("yaw_deg"), Yaw) || !FMath::IsFinite(Yaw))
            { Error = TEXT("local_frame requires position_cm and yaw_deg"); return false; }
        }
        else { Error = TEXT("Unknown semantic field: ") + FString(Field.Key); return false; }
    }
    FString Provenance;
    if (Value->TryGetStringField(TEXT("provenance"), Provenance) && Provenance != TEXT("design") && Provenance != TEXT("manual") && Provenance != TEXT("inferred"))
    { Error = TEXT("Unknown provenance"); return false; }
    return true;
}

void FLCSemanticWorld::Initialize(UWorld* InWorld, AActor* InternalHost)
{
    World = InWorld; Host = InternalHost; Session = FGuid::NewGuid().ToString(EGuidFormats::Digits);
    SpawnHandle = World->AddOnActorSpawnedHandler(FOnActorSpawned::FDelegate::CreateLambda([this](AActor* Actor) { PendingActors.Add(Actor); }));
    DestroyHandle = World->AddOnActorDestroyedHandler(FOnActorDestroyed::FDelegate::CreateRaw(this, &FLCSemanticWorld::Unregister));
    AddedHandle = FWorldDelegates::LevelAddedToWorld.AddRaw(this, &FLCSemanticWorld::LevelAdded);
    RemovedHandle = FWorldDelegates::LevelRemovedFromWorld.AddRaw(this, &FLCSemanticWorld::LevelRemoved);
    InstanceHandle = FInstancedStaticMeshDelegates::OnInstanceIndexUpdated.AddRaw(this, &FLCSemanticWorld::InstanceChanged);
    for (TActorIterator<AActor> It(World); It; ++It) Register(*It);
    Reindex();
}

void FLCSemanticWorld::Shutdown()
{
    if (!World) return;
    World->RemoveOnActorSpawnedHandler(SpawnHandle); World->RemoveOnActorDestroyedHandler(DestroyHandle);
    FWorldDelegates::LevelAddedToWorld.Remove(AddedHandle); FWorldDelegates::LevelRemovedFromWorld.Remove(RemovedHandle);
    FInstancedStaticMeshDelegates::OnInstanceIndexUpdated.Remove(InstanceHandle);
    World = nullptr;
}

void FLCSemanticWorld::Register(AActor* Actor)
{
    if (!IsValid(Actor) || Actor == Host.Get() || Actor->GetWorld() != World || Actor->Tags.Contains(TEXT("LCInternal"))) return;
    if (Actor->GetOwner() == Host.Get()) return;
    auto* Identity = Actor->FindComponentByClass<ULCIdentityComponent>();
    if (Identity && Identity->ExcludeFromRegistry) return;
    TInlineComponentArray<UPrimitiveComponent*> Primitives(Actor);
    if (Primitives.IsEmpty() && !Identity && !Actor->GetRootComponent()) return;
    for (const auto& Pair : Bindings) if (Pair.Value.Actor.Get() == Actor && Pair.Value.Index == INDEX_NONE) return;
    FString Id = Identity ? Identity->PersistentId : TEXT("");
    const bool Persistent = LCValidId(Id);
    bool Conflict = Persistent && (Rows.Contains(Id) || External.Contains(Id));
    if (!Persistent || Conflict) Id = TEXT("observed-") + FGuid::NewGuid().ToString(EGuidFormats::Digits);
    TSharedPtr<FJsonObject> Row = MakeShared<FJsonObject>();
    Row->SetStringField(TEXT("id"), Id); Row->SetStringField(TEXT("source"), TEXT("world"));
    Row->SetStringField(TEXT("name"), Identity && !Identity->DisplayName.IsEmpty() ? Identity->DisplayName : TEXT("Unclassified object"));
    Row->SetStringField(TEXT("role"), Identity && !Identity->Role.IsEmpty() ? Identity->Role : TEXT("unclassified"));
    Row->SetStringField(TEXT("area"), Identity ? Identity->Area : TEXT(""));
    Row->SetStringField(TEXT("provenance"), Identity ? TEXT("manual") : TEXT("inferred"));
    Row->SetStringField(TEXT("identity_scope"), Persistent && !Conflict ? TEXT("persistent") : TEXT("session"));
    Row->SetBoolField(TEXT("identity_conflict"), Conflict);
    Row->SetArrayField(TEXT("capabilities"), Strings({TEXT("read"), TEXT("annotate")}));
    if (Identity)
    {
        TArray<TSharedPtr<FJsonValue>> Aliases;
        for (const auto& Alias : Identity->Aliases) Aliases.Add(MakeShared<FJsonValueString>(Alias));
        Row->SetArrayField(TEXT("aliases"), Aliases);
    }
    External.Add(Id, Row); Rows.Add(Id, Row); Bindings.Add(Id, {Actor, nullptr, INDEX_NONE}); Sample(Id);
    ApplySavedAnnotation(Id);
    for (auto* Primitive : Primitives) if (auto* Instances = Cast<UInstancedStaticMeshComponent>(Primitive))
        for (int32 IndexValue = 0; IndexValue < Instances->GetInstanceCount(); ++IndexValue) RegisterInstance(Instances, IndexValue, true);
    ++RegistryRevision;
}

void FLCSemanticWorld::RegisterInstance(UInstancedStaticMeshComponent* Component, int32 IndexValue, bool Authored)
{
    if (!Component || Component->GetOwner() == Host.Get()) return;
    FString Parent;
    for (const auto& Pair : Bindings)
    {
        if (Pair.Value.Instances.Get() == Component && Pair.Value.Index == IndexValue) return;
        if (Pair.Value.Actor.Get() == Component->GetOwner() && Pair.Value.Index == INDEX_NONE) Parent = FString(Pair.Key);
    }
    if (Parent.IsEmpty()) return;
    FString Id;
    if (auto* Identity = Component->GetOwner()->FindComponentByClass<ULCIdentityComponent>(); Authored && Identity)
        for (const auto& Member : Identity->Instances) if (Member.Component == Component && Member.InitialIndex == IndexValue) { Id = Member.PersistentId; break; }
    const bool Persistent = LCValidId(Id) && !Rows.Contains(Id);
    if (!Persistent) Id = TEXT("observed-") + FGuid::NewGuid().ToString(EGuidFormats::Digits);
    TSharedPtr<FJsonObject> Row = MakeShared<FJsonObject>();
    Row->SetStringField(TEXT("id"), Id); Row->SetStringField(TEXT("parent"), Parent);
    Row->SetStringField(TEXT("name"), TEXT("Unclassified instance")); Row->SetStringField(TEXT("role"), TEXT("unclassified"));
    Row->SetStringField(TEXT("source"), TEXT("world")); Row->SetStringField(TEXT("provenance"), TEXT("inferred"));
    Row->SetStringField(TEXT("identity_scope"), Persistent ? TEXT("persistent") : TEXT("session"));
    Row->SetArrayField(TEXT("capabilities"), Strings({TEXT("read"), TEXT("annotate")}));
    External.Add(Id, Row); Rows.Add(Id, Row); Bindings.Add(Id, {Component->GetOwner(), Component, IndexValue}); Sample(Id);
    ApplySavedAnnotation(Id);
    ++RegistryRevision;
}

void FLCSemanticWorld::Unregister(AActor* Actor)
{ RemoveActor(Actor, TEXT("deleted")); }

void FLCSemanticWorld::ApplySavedAnnotation(const FString& Id)
{
    if (!External.Contains(Id)) return;
    auto Row = LCParseJson(LCJsonString(External[Id])); const TSharedPtr<FJsonObject>* Annotation = nullptr;
    if (SavedNodes && SavedNodes->TryGetObjectField(Id, Annotation))
        for (const auto& Field : (*Annotation)->Values) Row->SetField(Field.Key, Field.Value);
    Rows.Add(Id, Row);
}

void FLCSemanticWorld::RemoveActor(AActor* Actor, const FString& Reason)
{
    TArray<FString> Removed;
    for (const auto& Pair : Bindings) if (Pair.Value.Actor.Get() == Actor || !Pair.Value.Actor.IsValid()) Removed.Add(FString(Pair.Key));
    for (const auto& Id : Removed) { Bindings.Remove(Id); External.Remove(Id); Rows.Remove(Id); Live.Remove(Id); Event(Id, Reason); }
    if (!Removed.IsEmpty()) { ++RegistryRevision; Reindex(); }
}

void FLCSemanticWorld::LevelAdded(ULevel* Level, UWorld* InWorld)
{ if (World == InWorld && Level) { for (const auto& Actor : Level->Actors) Register(Actor.Get()); Reindex(); } }
void FLCSemanticWorld::LevelRemoved(ULevel* Level, UWorld* InWorld)
{ if (World == InWorld && Level) for (const auto& Actor : Level->Actors) RemoveActor(Actor.Get(), TEXT("unloaded")); }

void FLCSemanticWorld::InstanceChanged(UInstancedStaticMeshComponent* Component, TArrayView<const FInstancedStaticMeshDelegates::FInstanceIndexUpdateData> Updates)
{
    using Kind = FInstancedStaticMeshDelegates::EInstanceIndexUpdateType;
    if (!Component || Component->GetOwner() == Host.Get()) return;
    for (const auto& Update : Updates)
    {
        if (Update.Type == Kind::Added) { RegisterInstance(Component, Update.Index); continue; }
        TArray<FString> Removed;
        for (auto& Pair : Bindings) if (Pair.Value.Instances.Get() == Component)
        {
            if (Update.Type == Kind::Relocated && Pair.Value.Index == Update.OldIndex) Pair.Value.Index = Update.Index;
            else if (Update.Type == Kind::Cleared || Update.Type == Kind::Destroyed || (Update.Type == Kind::Removed && Pair.Value.Index == Update.Index)) Removed.Add(FString(Pair.Key));
        }
        for (const auto& Id : Removed) { Bindings.Remove(Id); Rows.Remove(Id); External.Remove(Id); Live.Remove(Id); Event(Id, TEXT("deleted")); }
    }
    ++RegistryRevision; Reindex();
}

void FLCSemanticWorld::Event(const FString& Id, const FString& Kind, const TSharedPtr<FJsonObject>& State)
{
    TSharedPtr<FJsonObject> Value = MakeShared<FJsonObject>(); Value->SetNumberField(TEXT("sequence"), ++Sequence);
    Value->SetStringField(TEXT("id"), Id); Value->SetStringField(TEXT("kind"), Kind);
    if (State) Value->SetObjectField(TEXT("state"), State);
    LiveEvents.Add(MakeShared<FJsonValueObject>(Value));
    if (LiveEvents.Num() > 4096) LiveEvents.RemoveAt(0, 1024, EAllowShrinking::No);
}

void FLCSemanticWorld::Observe(const FString& Id, const TSharedPtr<FJsonObject>& Transform, const FString& Lifecycle)
{
    TSharedPtr<FJsonObject> State = MakeShared<FJsonObject>(); State->SetObjectField(TEXT("transform"), Transform);
    State->SetStringField(TEXT("lifecycle"), Lifecycle);
    FLCEntity Geometry;
    if (LCReadVector(Transform, TEXT("position_cm"), Geometry.Position) && LCReadVector(Transform, TEXT("size_cm"), Geometry.Size))
    {
        LCReadVector(Transform, TEXT("rotation_deg"), Geometry.Rotation);
        auto Bounds = MakeShared<FJsonObject>(); Bounds->SetArrayField(TEXT("min_cm"), LCVector(Geometry.Bounds().Min)); Bounds->SetArrayField(TEXT("max_cm"), LCVector(Geometry.Bounds().Max));
        State->SetObjectField(TEXT("bounds"), Bounds);
    }
    if (Live.Contains(Id))
    {
        auto Before = Live[Id];
        if (Before->GetStringField(TEXT("lifecycle")) == Lifecycle && LCJsonString(Before->GetObjectField(TEXT("transform"))) == LCJsonString(Transform)) return;
    }
    State->SetNumberField(TEXT("observed_s"), FPlatformTime::Seconds()); State->SetNumberField(TEXT("sequence"), Sequence + 1);
    Event(Id, Live.Contains(Id) ? TEXT("changed") : TEXT("added"), State); Live.Add(Id, State);
}

void FLCSemanticWorld::Sample(const FString& Id)
{
    auto* Binding = Bindings.Find(Id);
    if (!Binding || !Binding->Actor.IsValid()) return;
    auto* Actor = Binding->Actor.Get(); FTransform Transform = Actor->GetActorTransform(); FBox Box = Actor->GetComponentsBoundingBox(true);
    if (Binding->Instances.IsValid())
    {
        auto* Instances = Binding->Instances.Get();
        if (!Instances->GetInstanceTransform(Binding->Index, Transform, true)) return;
        if (Instances->GetStaticMesh()) Box = Instances->GetStaticMesh()->GetBoundingBox().TransformBy(Transform);
    }
    TSharedPtr<FJsonObject> Geometry = MakeShared<FJsonObject>();
    const auto Rotation = Transform.Rotator();
    Geometry->SetArrayField(TEXT("position_cm"), LCVector(Transform.GetLocation()));
    Geometry->SetArrayField(TEXT("rotation_deg"), LCVector(FVector(Rotation.Pitch, Rotation.Yaw, Rotation.Roll)));
    Geometry->SetArrayField(TEXT("scale"), LCVector(Transform.GetScale3D()));
    Geometry->SetArrayField(TEXT("size_cm"), LCVector(Box.IsValid ? Box.GetSize() : FVector::ZeroVector));
    Geometry->SetArrayField(TEXT("velocity_cm_s"), LCVector(Actor->GetVelocity()));
    Geometry->SetBoolField(TEXT("collision"), Actor->GetActorEnableCollision());
    Geometry->SetBoolField(TEXT("hidden"), Actor->IsHidden());
    if (auto* Primitive = Cast<UPrimitiveComponent>(Actor->GetRootComponent())) Geometry->SetBoolField(TEXT("simulating_physics"), Primitive->IsSimulatingPhysics());
    if (auto* Identity = Actor->FindComponentByClass<ULCIdentityComponent>())
    {
        TSharedPtr<FJsonObject> Values = MakeShared<FJsonObject>();
        TArray<FString> Keys; Identity->State.GetKeys(Keys); Keys.Sort();
        for (const auto& Key : Keys) Values->SetStringField(Key, Identity->State[Key]);
        Geometry->SetObjectField(TEXT("adapter_state"), Values);
    }
    Observe(Id, Geometry, TEXT("active"));
    TSharedPtr<FJsonObject> Bounds = MakeShared<FJsonObject>(); Bounds->SetArrayField(TEXT("min_cm"), LCVector(Box.IsValid ? Box.Min : Transform.GetLocation()));
    Bounds->SetArrayField(TEXT("max_cm"), LCVector(Box.IsValid ? Box.Max : Transform.GetLocation()));
    Live[Id]->SetObjectField(TEXT("bounds"), Bounds);
    if (const TSharedPtr<FJsonObject>* ExternalRow = External.Find(Id))
    {
        if (!(*ExternalRow)->HasField(TEXT("transform")))
        {
            (*ExternalRow)->SetObjectField(TEXT("transform"), Geometry);
            (*ExternalRow)->SetObjectField(TEXT("bounds"), Bounds);
        }
    }
}

void FLCSemanticWorld::Tick()
{
    if (!PendingActors.IsEmpty())
    { auto Actors = MoveTemp(PendingActors); for (const auto& Actor : Actors) Register(Actor.Get()); Reindex(); }
    if (FPlatformTime::Seconds() < NextSample) return;
    const double Start = FPlatformTime::Seconds();
    if (SampleCursor == 0) Bindings.GetKeys(SampleIds);
    while (SampleCursor < SampleIds.Num() && FPlatformTime::Seconds() - Start < 0.001)
    { const FString Id = SampleIds[SampleCursor++]; if (Bindings.Contains(Id) && Bindings[Id].Actor.IsValid()) Sample(Id); }
    if (SampleCursor >= SampleIds.Num()) { SampleCursor = 0; NextSample = FPlatformTime::Seconds() + 0.1; }
}

void FLCSemanticWorld::Reindex()
{
    Index.Reset();
    for (const auto& Pair : Rows)
    {
        for (auto Key : {TEXT("role"), TEXT("source"), TEXT("area"), TEXT("pattern"), TEXT("ring"), TEXT("side"), TEXT("parent"), TEXT("name")})
        { FString Value; if (Pair.Value->TryGetStringField(Key, Value)) Index.FindOrAdd(FString(Key) + TEXT("=") + Value).Add(FString(Pair.Key)); }
        TArray<FString> Aliases; Pair.Value->TryGetStringArrayField(TEXT("aliases"), Aliases);
        for (const auto& Alias : Aliases) Index.FindOrAdd(TEXT("name=") + Alias).Add(FString(Pair.Key));
    }
}

bool FLCSemanticWorld::Descendant(const FString& Id, const FString& Parent) const
{
    FString Current = Id; TSet<FString> Seen;
    while (const auto* Row = Rows.Find(Current))
    {
        if (Current == Parent) return true;
        if (Seen.Contains(Current)) return false;
        Seen.Add(Current);
        if (!(*Row)->TryGetStringField(TEXT("parent"), Current)) return false;
    }
    return false;
}

void FLCSemanticWorld::Publish(const TMap<FString, FLCEntity>& Entities, const TSharedPtr<FJsonObject>& State, int64 Version)
{
    Rows.Reset(); for (const auto& Pair : External) Rows.Add(FString(Pair.Key), LCParseJson(LCJsonString(Pair.Value)));
    Physical.Reset(); CommittedVersion = Version;
    const auto Nodes = State->GetObjectField(TEXT("nodes"));
    SavedNodes = Nodes;
    for (const auto& Pair : Nodes->Values)
    {
        auto Row = LCParseJson(LCJsonString(Pair.Value->AsObject()));
        if (Rows.Contains(FString(Pair.Key)))
        { for (const auto& Field : Row->Values) if (FString(Field.Key) != TEXT("capabilities")) Rows[FString(Pair.Key)]->SetField(FString(Field.Key), Field.Value); }
        else if (Row->GetStringField(TEXT("source")) != TEXT("world"))
        { Row->SetArrayField(TEXT("capabilities"), Strings({TEXT("read"), TEXT("annotate"), TEXT("move"), TEXT("delete"), TEXT("surface")})); Rows.Add(FString(Pair.Key), Row); }
    }
    for (const auto& Pair : Entities)
    {
        auto Row = Pair.Value.ToJson();
        if (Pair.Value.Semantic) for (const auto& Field : Pair.Value.Semantic->Values) Row->SetField(FString(Field.Key), Field.Value);
        Row->SetStringField(TEXT("id"), FString(Pair.Key)); Row->SetStringField(TEXT("source"), Pair.Value.Source);
        if (!Row->HasField(TEXT("role"))) Row->SetStringField(TEXT("role"), TEXT("unclassified"));
        Row->SetStringField(TEXT("identity_scope"), TEXT("persistent"));
        Row->SetArrayField(TEXT("capabilities"), Strings({TEXT("read"), TEXT("annotate"), TEXT("move"), TEXT("delete"), TEXT("surface"), TEXT("set")}));
        Rows.Add(FString(Pair.Key), Row); Physical.Add(FString(Pair.Key));
        Observe(FString(Pair.Key), Row->GetObjectField(TEXT("transform")), TEXT("committed"));
    }
    TMap<FString, FBox> Boxes;
    for (const auto& Pair : Entities)
    {
        FString Parent; if (Pair.Value.Semantic) Pair.Value.Semantic->TryGetStringField(TEXT("parent"), Parent);
        TSet<FString> Seen;
        while (Rows.Contains(Parent) && !Seen.Contains(Parent))
        { Seen.Add(Parent); auto* Box = Boxes.Find(Parent); if (!Box) Boxes.Add(Parent, Pair.Value.Bounds()); else *Box += Pair.Value.Bounds(); if (!Rows[Parent]->TryGetStringField(TEXT("parent"), Parent)) break; }
    }
    for (const auto& Pair : Boxes)
    {
        TSharedPtr<FJsonObject> Bounds = MakeShared<FJsonObject>(); Bounds->SetArrayField(TEXT("min_cm"), LCVector(Pair.Value.Min)); Bounds->SetArrayField(TEXT("max_cm"), LCVector(Pair.Value.Max));
        Rows[FString(Pair.Key)]->SetObjectField(TEXT("bounds"), Bounds);
        TSharedPtr<FJsonObject> Transform = MakeShared<FJsonObject>(); Transform->SetArrayField(TEXT("position_cm"), LCVector(Pair.Value.GetCenter())); Transform->SetArrayField(TEXT("size_cm"), LCVector(Pair.Value.GetSize()));
        Rows[FString(Pair.Key)]->SetObjectField(TEXT("transform"), Transform);
    }
    TArray<FString> Removed;
    for (const auto& Pair : Live) if (!Rows.Contains(FString(Pair.Key))) Removed.Add(FString(Pair.Key));
    for (const auto& Id : Removed) { Live.Remove(Id); Event(Id, TEXT("deleted")); }
    Reindex();
}

bool FLCSemanticWorld::Expand(const FString& Id, TArray<FString>& Ids, FString& Error) const
{
    if (!Rows.Contains(Id)) { Error = TEXT("Unknown identity: ") + Id; return false; }
    if (Bindings.Contains(Id)) { Error = TEXT("Identity adapter exposes read/annotate only: ") + Id; return false; }
    if (Physical.Contains(Id)) { Ids.AddUnique(Id); return true; }
    bool Found = false;
    for (const auto& Leaf : Physical) if (Descendant(Leaf, Id)) { Ids.AddUnique(Leaf); Found = true; }
    if (!Found) { Error = TEXT("Identity has no editable geometry: ") + Id; return false; }
    return true;
}

TSharedPtr<FJsonObject> FLCSemanticWorld::Frame() const
{
    TSharedPtr<FJsonObject> Result = MakeShared<FJsonObject>();
    auto* Controller = UGameplayStatics::GetPlayerController(World, 0);
    if (!Controller || !Controller->PlayerCameraManager) return Result;
    auto* Camera = Controller->PlayerCameraManager.Get();
    const FRotationMatrix Basis(Camera->GetCameraRotation());
    Result->SetArrayField(TEXT("position_cm"), LCVector(Camera->GetCameraLocation()));
    Result->SetArrayField(TEXT("forward"), LCVector(Basis.GetUnitAxis(EAxis::X)));
    Result->SetArrayField(TEXT("right"), LCVector(Basis.GetUnitAxis(EAxis::Y)));
    Result->SetArrayField(TEXT("up"), LCVector(Basis.GetUnitAxis(EAxis::Z)));
    Result->SetNumberField(TEXT("observed_s"), FPlatformTime::Seconds());
    return Result;
}

bool FLCSemanticWorld::ResolveSelection(const FString& Token, int64 Version, TArray<FString>& Ids, FString& Error)
{
    const auto* Selection = Selections.Find(Token);
    if (!Selection || Selection->Version != Version || Selection->Expires < FPlatformTime::Seconds())
    { Error = TEXT("Selection expired; query again"); return false; }
    for (const auto& Id : Selection->Ids)
    {
        if (Bindings.Contains(Id)) Sample(Id);
        if (!Rows.Contains(Id)) { Error = TEXT("Selected identity no longer exists"); return false; }
        const FString Current = Live.Contains(Id) ? LCJsonString(Live[Id]->GetObjectField(TEXT("transform"))) : TEXT("");
        if (Current != Selection->Fingerprints.FindRef(Id)) { Error = TEXT("Selected object changed; query again"); return false; }
        if (!Expand(Id, Ids, Error)) return false;
    }
    Ids.Sort(); return true;
}
