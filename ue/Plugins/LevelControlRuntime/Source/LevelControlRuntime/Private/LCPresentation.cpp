#include "LCPresentation.h"
#include "LCSurface.h"
#include "Components/DecalComponent.h"
#include "LCWireframeComponent.h"
#include "Camera/CameraActor.h"
#include "Camera/CameraComponent.h"
#include "GameFramework/PlayerController.h"
#include "GameFramework/Character.h"
#include "Components/CapsuleComponent.h"
#include "Engine/World.h"
#include "Engine/StaticMesh.h"
#include "Kismet/GameplayStatics.h"
#include "Components/AudioComponent.h"
#include "LCMusicWave.h"
#include "Interfaces/IPluginManager.h"
#include "AudioDevice.h"
#include "LCSettings.h"
#include "Misc/CommandLine.h"
#include "Misc/Parse.h"
#include "Materials/MaterialInstanceDynamic.h"
#include "InputCoreTypes.h"

bool ULCPresentation::Initialize(AActor* Owner)
{
    Host = Owner;
    AudioOutputLatencyMs = GetDefault<ULCSettings>()->AudioOutputLatencyMs;
    FParse::Value(FCommandLine::Get(), TEXT("LCAudioLatencyMs="), AudioOutputLatencyMs);
    AudioOutputLatencyMs = FMath::Clamp(AudioOutputLatencyMs, 0.0, 300.0);
    Wireframe = NewObject<ULCWireframeComponent>(Host);
    Wireframe->SetupAttachment(Host->GetRootComponent());
    Host->AddInstanceComponent(Wireframe);
    Wireframe->RegisterComponent();
    // Warm preview materials before a timed presentation starts.
    for (const auto* Name : {TEXT("M_Growth_Surface"), TEXT("M_Growth_Glass"), TEXT("M_Growth_Default"), TEXT("M_Growth_Decal")})
        LoadObject<UMaterialInterface>(nullptr, *(FString(TEXT("/Game/LC2/Materials/")) + Name));
    Camera = Host->GetWorld()->SpawnActor<ACameraActor>();
    if (!Camera) return false;
    Camera->SetOwner(Host);
    Camera->SetFlags(RF_Transient);
    Camera->GetCameraComponent()->SetFieldOfView(65);
    MusicAudio = NewObject<UAudioComponent>(Host);
    MusicAudio->SetupAttachment(Host->GetRootComponent());
    MusicAudio->bAutoActivate = false; MusicAudio->bIsUISound = true;
    MusicAudio->bAllowSpatialization = false;
    MusicAudio->SetVolumeMultiplier(0.65f);
    Host->AddInstanceComponent(MusicAudio); MusicAudio->RegisterComponent();
    return true;
}

static bool LCShotValid(const TSharedPtr<FJsonObject>& Shot)
{
    FVector Position, Target;
    return LCReadVector(Shot, TEXT("position_cm"), Position) && LCReadVector(Shot, TEXT("target_cm"), Target)
        && !Position.Equals(Target, 1);
}

bool ULCPresentation::Publish(const TSharedPtr<FJsonObject>& Request, FString& Error)
{
    bool Incremental = false, NewDirector = false, Seal = false;
    double GrowthDuration = 3;
    FString NewCaption;
    if ((Request->HasField(TEXT("incremental")) && !Request->TryGetBoolField(TEXT("incremental"), Incremental)) ||
        (Request->HasField(TEXT("director")) && !Request->TryGetBoolField(TEXT("director"), NewDirector)) ||
        (Request->HasField(TEXT("seal")) && !Request->TryGetBoolField(TEXT("seal"), Seal)) ||
        (Request->HasField(TEXT("growth_duration_s")) && !Request->TryGetNumberField(TEXT("growth_duration_s"), GrowthDuration)) ||
        (Request->HasField(TEXT("caption")) && !Request->TryGetStringField(TEXT("caption"), NewCaption)) ||
        !FMath::IsFinite(GrowthDuration) || GrowthDuration < 0.05 || GrowthDuration > 10 || NewCaption.Len() > 160)
    { Error = TEXT("Invalid incremental growth options"); return false; }
    FString NewSource;
    double NewRevision;
    bool NewInstant = false;
    if (Request->HasField(TEXT("instant")) && !Request->TryGetBoolField(TEXT("instant"), NewInstant))
    { Error = TEXT("instant must be boolean"); return false; }
    bool NewAutoSkip = false;
    double NewFinishDelay = 1;
    if ((Request->HasField(TEXT("auto_skip")) && !Request->TryGetBoolField(TEXT("auto_skip"), NewAutoSkip)) ||
        (Request->HasField(TEXT("finish_delay_s")) && !Request->TryGetNumberField(TEXT("finish_delay_s"), NewFinishDelay)) ||
        !FMath::IsFinite(NewFinishDelay) || NewFinishDelay < 0 || NewFinishDelay > 1)
    { Error = TEXT("Invalid auto_skip or finish_delay_s"); return false; }
    const bool HasAnimateIds = Request->HasField(TEXT("animate_ids"));
    TSet<FString> AnimateIds;
    if (HasAnimateIds)
    {
        const TArray<TSharedPtr<FJsonValue>>* Values = nullptr;
        if (!Request->TryGetArrayField(TEXT("animate_ids"), Values) || Values->Num() > 16384)
        { Error = TEXT("animate_ids must be a bounded ID array"); return false; }
        for (const auto& Value : *Values)
        {
            if (Value->Type != EJson::String || !LCValidId(Value->AsString()) || AnimateIds.Contains(Value->AsString()))
            { Error = TEXT("Invalid or duplicate animate_ids"); return false; }
            AnimateIds.Add(Value->AsString());
        }
    }
    const TArray<TSharedPtr<FJsonValue>> *Regions, *Entities, *Timeline;
    if (!Request->TryGetStringField(TEXT("source"), NewSource) || !LCValidId(NewSource) ||
        !Request->TryGetNumberField(TEXT("revision"), NewRevision) || !FMath::IsFinite(NewRevision) ||
        NewRevision <= Revision || NewRevision > 1e9 || FMath::FloorToDouble(NewRevision) != NewRevision ||
        !Request->TryGetArrayField(TEXT("regions"), Regions) || Regions->Num() > 256 ||
        !Request->TryGetArrayField(TEXT("entities"), Entities) || Entities->Num() > 16384 ||
        !Request->TryGetArrayField(TEXT("timeline"), Timeline) || Timeline->Num() != Entities->Num())
    { Error = TEXT("Invalid preview source, increasing revision, regions, entities or timeline"); return false; }
    if (Incremental && (NewSource != Source || (State != TEXT("previewing") && State != TEXT("settled"))))
    { Error = TEXT("Incremental preview requires the current active source"); return false; }
    TArray<FBox> Boxes;
    TArray<FLCGrowthRegion> NewRegions;
    for (const auto& Value : *Regions)
    {
        FVector Center, Size;
        if (Value->Type != EJson::Object || !LCReadVector(Value->AsObject(), TEXT("center_cm"), Center) ||
            !LCReadVector(Value->AsObject(), TEXT("size_cm"), Size) || Size.GetMin() <= 0)
        { Error = TEXT("Invalid preview region"); return false; }
        Boxes.Add(FBox(Center - Size / 2, Center + Size / 2));
        FLCGrowthRegion Region;
        Region.Id = FString::Printf(TEXT("region-%d"), NewRegions.Num());
        if ((Value->AsObject()->HasField(TEXT("id")) && !Value->AsObject()->TryGetStringField(TEXT("id"), Region.Id)) ||
            (Value->AsObject()->HasField(TEXT("label")) && !Value->AsObject()->TryGetStringField(TEXT("label"), Region.Label)) ||
            !LCValidId(Region.Id) || Region.Label.Len() > 80 || NewRegions.ContainsByPredicate([&](const FLCGrowthRegion& R) { return R.Id == Region.Id; }))
        { Error = TEXT("Invalid preview region id or label"); return false; }
        Region.Bounds = Boxes.Last(); Region.Duration = GrowthDuration;
        NewRegions.Add(Region);
    }
    TArray<FLCFlight> NewFlights;
    TSet<FString> Ids;
    for (int32 Index = 0; Index < Entities->Num(); ++Index)
    {
        FLCFlight Flight;
        const auto& Entity = (*Entities)[Index];
        const auto& Timing = (*Timeline)[Index];
        FString Id;
        if (Entity->Type != EJson::Object || !FLCEntity::FromJson(Entity->AsObject(), Flight.Entity, Error)) return false;
        Flight.Entity.Source = NewSource;
        Flight.Animate = !HasAnimateIds || AnimateIds.Contains(Flight.Entity.Id);
        if (Ids.Contains(Flight.Entity.Id) || Timing->Type != EJson::Object ||
            !Timing->AsObject()->TryGetStringField(TEXT("id"), Id) || Id != Flight.Entity.Id ||
            !Timing->AsObject()->TryGetNumberField(TEXT("start_s"), Flight.Start) ||
            !Timing->AsObject()->TryGetNumberField(TEXT("duration_s"), Flight.Duration) ||
            !FMath::IsFinite(Flight.Start) || !FMath::IsFinite(Flight.Duration) ||
            Flight.Start < 0 || Flight.Start > 110 ||
            ((NewInstant || !Flight.Animate) ? Flight.Start != 0 || Flight.Duration != 0 : Flight.Duration < 0.05 || Flight.Start + Flight.Duration > 120))
        { Error = TEXT("Invalid or duplicate preview timing"); return false; }
        Ids.Add(Flight.Entity.Id);
        NewFlights.Add(Flight);
    }
    for (const auto& Id : AnimateIds) if (!Ids.Contains(Id))
    { Error = TEXT("animate_ids contains an unknown entity"); return false; }
    TSharedPtr<FJsonObject> NewCameras;
    const TSharedPtr<FJsonObject>* CameraData;
    if (Request->HasField(TEXT("cameras")))
    {
        const TSharedPtr<FJsonObject> *Overview, *Hero, *Groups;
        if (!Request->TryGetObjectField(TEXT("cameras"), CameraData) ||
            !(*CameraData)->TryGetObjectField(TEXT("overview"), Overview) || !LCShotValid(*Overview) ||
            !(*CameraData)->TryGetObjectField(TEXT("hero"), Hero) || !LCShotValid(*Hero) ||
            !(*CameraData)->TryGetObjectField(TEXT("groups"), Groups) || (*Groups)->Values.Num() > 256)
        { Error = TEXT("Invalid preview cameras"); return false; }
        for (const auto& Group : (*Groups)->Values)
            if (Group.Value->Type != EJson::Object || !LCShotValid(Group.Value->AsObject()))
            { Error = TEXT("Invalid group camera"); return false; }
        NewCameras = *CameraData;
    }
    FString NewMusic = TEXT("none");
    Request->TryGetStringField(TEXT("music"), NewMusic);
    if (NewMusic != TEXT("none") && NewMusic != TEXT("architect-pulse"))
    { Error = TEXT("Unknown music id"); return false; }
    if (NewInstant && (NewCameras || NewMusic != TEXT("none")))
    { Error = TEXT("Instant preview cannot own a camera or play music"); return false; }
    ULCMusicWave* NewWave = nullptr;
    if (NewMusic != TEXT("none"))
    {
        if (!Host->GetWorld()->GetAudioDevice().IsValid()) { Error = TEXT("Music requires an audio device"); return false; }
        NewWave = NewObject<ULCMusicWave>(this);
        const auto Plugin = IPluginManager::Get().FindPlugin(TEXT("LevelControlRuntime"));
        if (!Plugin || !NewWave->Load(Plugin->GetBaseDir() / TEXT("Resources/architect-pulse.wav")))
        { Error = TEXT("Music PCM resource is unavailable"); return false; }
    }
    const double Now = FPlatformTime::Seconds();
    if (!Incremental)
    {
        if (State == TEXT("previewing") || State == TEXT("settled")) Event(TEXT("cancelled"));
        ClearGeometry(); Flights.Reset(); GrowthGroups.Reset(); GrowthRegions.Reset();
        ReturnCamera(); CameraReleased = false;
        Started = Now; Ended = 0; FirstVisible = -1;
        MaxTickMs = 0; MaxFrameMs = 0; FrameTotal = 0; FrameCount = 0; MaxBeatErrorMs = 0;
        MusicAudio->Stop(); MusicWave = NewWave; Music = NewMusic;
        if (MusicWave) { MusicAudio->SetSound(MusicWave); MusicAudio->Play(); }
        CurrentGroup.Reset(); SkipReason.Reset(); HandoffCount = 0;
    }
    TMap<FString, int32> OldItems;
    for (int32 Index = 0; Index < Flights.Num(); ++Index) OldItems.Add(Flights[Index].Entity.Id, Index);
    TSet<FString> Preserved;
    for (auto& Item : NewFlights)
    {
        const int32* OldIndex = OldItems.Find(Item.Entity.Id);
        if (Incremental && OldIndex &&
            LCJsonString(Flights[*OldIndex].Entity.ToJson()) == LCJsonString(Item.Entity.ToJson()))
        {
            Preserved.Add(Item.Entity.Id); Item = Flights[*OldIndex]; continue;
        }
        if (!Item.Animate) { Item.Settled = true; continue; }
        const FString Name = Item.Entity.Group.IsEmpty() ? TEXT("default") : Item.Entity.Group;
        Item.GrowthKey = LexToString(static_cast<int64>(NewRevision)) + TEXT(":") + Name;
        Item.BatchKey = Item.GrowthKey + TEXT("|") + LCBatchKey(Item.Entity);
        FLCGrowthGroup* Existing = GrowthGroups.Find(Item.GrowthKey);
        if (!Existing)
        {
            FLCGrowthGroup Group; Group.Name = Name; Group.Delay = Item.Start;
            Group.Duration = Item.Duration; GrowthGroups.Add(Item.GrowthKey, Group);
        }
        auto& Group = GrowthGroups[Item.GrowthKey];
        const double End = FMath::Max(Group.Delay + Group.Duration, Item.Start + Item.Duration);
        Group.Delay = FMath::Min(Group.Delay, Item.Start); Group.Duration = End - Group.Delay;
        Group.Bounds += Item.Entity.Bounds(); ++Group.Count;
        if (Item.Entity.Template.StartsWith(TEXT("decal/"))) Group.DecalIds.Add(Item.Entity.Id);
        else Group.BatchKeys.Add(Item.BatchKey);
    }
    for (const auto& Old : Flights) if (!Preserved.Contains(Old.Entity.Id)) RemoveFlight(Old);
    TSet<FString> UsedGroups, UsedBatches;
    for (const auto& Item : NewFlights)
    {
        if (!Item.GrowthKey.IsEmpty()) UsedGroups.Add(Item.GrowthKey);
        if (!Item.BatchKey.IsEmpty()) UsedBatches.Add(Item.BatchKey);
    }
    for (auto It = GrowthGroups.CreateIterator(); It; ++It)
        if (!UsedGroups.Contains(It.Key())) It.RemoveCurrent();
    for (auto& Pair : GrowthGroups) { Pair.Value.Count = 0; Pair.Value.Prepared = 0; }
    for (const auto& Item : NewFlights)
        if (auto* Group = GrowthGroups.Find(Item.GrowthKey))
        { ++Group->Count; if (Item.Created) ++Group->Prepared; }
    for (auto It = Batches.CreateIterator(); It; ++It)
        if (!UsedBatches.Contains(It.Key())) { It.Value()->DestroyComponent(); It.RemoveCurrent(); }
    GroupLabels.Reset();
    for (auto& Region : NewRegions)
    {
        Region.Started = Now;
        if (Incremental)
            if (const auto* Old = GrowthRegions.FindByPredicate([&](const FLCGrowthRegion& R) { return R.Id == Region.Id && R.Bounds == Region.Bounds; }))
            { Region.Started = Old->Started; Region.Duration = Old->Duration; Region.Progress = Old->Progress; }
        GroupLabels.Add(Region.Id, Region.Label.IsEmpty() ? Region.Id : Region.Label);
    }
    GrowthRegions = MoveTemp(NewRegions);
    Source = NewSource; Revision = static_cast<int64>(NewRevision);
    Flights = MoveTemp(NewFlights); Cameras = NewCameras; Director = NewDirector; Caption = NewCaption;
    FlightIndices.Reset();
    for (int32 Index = 0; Index < Flights.Num(); ++Index) FlightIndices.Add(Flights[Index].Entity.Id, Index);
    Instant = NewInstant || (Incremental && !SkipReason.IsEmpty());
    AutoSkip = NewAutoSkip; FinishDelay = NewFinishDelay; RevealBounds = Boxes;
    CameraEnabled = Cameras.IsValid() && !CameraReleased && !Instant;
    RegionCount = Boxes.Num(); AnimatedCount = 0; Settled = 0; Created = 0; Cursor = 0;
    for (const auto& Item : Flights)
    {
        if (Item.Animate) ++AnimatedCount;
        if (Item.Settled) ++Settled;
        if (Item.Created) ++Created;
    }
    if (Seal)
    {
        for (auto& Pair : GrowthGroups)
        {
            auto& Group = Pair.Value;
            Group.Delay = 0;
            if (Group.Started < 0) Group.Duration = FMath::Min(Group.Duration, 4.0);
            else if (Group.Progress < 1)
            {
                const double Remaining = FMath::Min(4.0, Group.Duration * (1 - Group.Progress));
                Group.Duration = Remaining / FMath::Max(0.001, 1 - Group.Progress);
                Group.Started = Now - Group.Progress * Group.Duration;
            }
        }
    }
    State = TEXT("previewing"); HeroUntil = 0;
    if (!Incremental && CameraEnabled) Shot(Cameras->GetObjectField(TEXT("overview")), TEXT("overview"));
    UpdateOverlay(); Event(Incremental ? TEXT("preview_updated") : TEXT("preview_published"));
    if (ShouldAutoSkip() && !Director)
    {
        const FString Reason = PlayerSkipReason();
        if (!Reason.IsEmpty()) Skip(Reason);
    }
    return true;
}

FString ULCPresentation::PlayerSkipReason() const
{
    const auto* Player = Host ? UGameplayStatics::GetPlayerCharacter(Host->GetWorld(), 0) : nullptr;
    if (!Player) return TEXT("player_unavailable");
    if (Player->GetVelocity().Size() > 10 || !Player->GetPendingMovementInputVector().IsNearlyZero())
        return TEXT("player_moving");
    const FBox PlayerBounds = Player->GetCapsuleComponent()->Bounds.GetBox();
    for (const auto& Box : RevealBounds) if (Box.Intersect(PlayerBounds)) return TEXT("player_inside_area");
    return TEXT("");
}

bool ULCPresentation::Skip(const FString& Reason)
{
    if (State != TEXT("previewing") && State != TEXT("settled") && State != TEXT("committed")) return false;
    Instant = true; SkipReason = Reason; HeroUntil = FPlatformTime::Seconds();
    MusicAudio->Stop(); MusicWave = nullptr; ReturnCamera();
    Event(TEXT("presentation_skipped"));
    return true;
}

TSharedPtr<FJsonObject> ULCPresentation::Event(const FString& Type)
{
    auto Json = MakeShared<FJsonObject>();
    Json->SetNumberField(TEXT("seq"), ++Sequence);
    Json->SetNumberField(TEXT("at_s"), FPlatformTime::Seconds());
    Json->SetStringField(TEXT("type"), Type);
    Json->SetStringField(TEXT("source"), Source);
    Json->SetNumberField(TEXT("revision"), Revision);
    Json->SetNumberField(TEXT("elapsed_s"), Started > 0 ? FPlatformTime::Seconds() - Started : 0);
    Json->SetNumberField(TEXT("max_frame_ms"), MaxFrameMs);
    Json->SetNumberField(TEXT("max_tick_ms"), MaxTickMs);
    EventLog.Add(MakeShared<FJsonValueObject>(Json));
    if (EventLog.Num() > 512) EventLog.RemoveAt(0);
    return Json;
}

void ULCPresentation::ClearGeometry()
{
    for (auto& Decal : Decals) Decal.Value->DestroyComponent();
    Decals.Reset();
    for (auto& Batch : Batches) Batch.Value->DestroyComponent();
    Batches.Reset(); GrowthGroups.Reset(); GrowthRegions.Reset();
    Wireframe->SetBoxes({});
    Created = 0; RegionCount = 0;
}

void ULCPresentation::Shot(const TSharedPtr<FJsonObject>& Data, const FString& Name)
{
    auto* Player = UGameplayStatics::GetPlayerController(Host->GetWorld(), 0);
    if (!Player || !Camera || !CameraEnabled || CameraReleased) return;
    if (!OwnsCamera)
    {
        PreviousView = Player->GetViewTarget(); OwnsCamera = true;
        FVector Position; FRotator Rotation; Player->GetPlayerViewPoint(Position, Rotation);
        Camera->SetActorLocationAndRotation(Position, Rotation);
        LastControlRotation = Player->GetControlRotation();
    }
    FVector Position, Target;
    LCReadVector(Data, TEXT("position_cm"), Position); LCReadVector(Data, TEXT("target_cm"), Target);
    CameraTargetLocation = Position; CameraTargetRotation = (Target - Position).Rotation();
    HasCameraTarget = true; LastCameraCut = FPlatformTime::Seconds();
    Player->SetViewTarget(Camera);
    CameraStage = Name;
}

void ULCPresentation::ReturnCamera()
{
    if (OwnsCamera && Host)
        if (auto* Player = UGameplayStatics::GetPlayerController(Host->GetWorld(), 0))
            Player->SetViewTarget(PreviousView.IsValid() ? PreviousView.Get() : Player->GetPawn());
    OwnsCamera = false; CameraEnabled = false; CameraReleased = true; HasCameraTarget = false;
    PreviousView.Reset(); CameraStage = TEXT("player");
}

void ULCPresentation::Cancel()
{
    if (MusicAudio) MusicAudio->Stop();
    ClearGeometry(); ReturnCamera(); Flights.Reset();
    State = TEXT("cancelled"); HeroUntil = 0; Ended = FPlatformTime::Seconds(); Event(TEXT("cancelled"));
}

void ULCPresentation::Committed()
{
    ClearGeometry(); State = TEXT("committed");
    if (Cameras && CameraEnabled) Shot(Cameras->GetObjectField(TEXT("hero")), TEXT("hero"));
    HeroUntil = FPlatformTime::Seconds() + (Instant ? 0.0 : FinishDelay);
    Event(TEXT("committed"));
}

void ULCPresentation::Handoff(const FString& EntityId)
{
    const int32* Index = FlightIndices.Find(EntityId);
    if (!Index || State != TEXT("settled")) return;
    auto& Item = Flights[*Index];
    if (!Item.Created) return;
    // The matching real instance is already present in this frame. Avoid double opacity.
    RemoveFlight(Item); Item.Created = false; --Created; ++HandoffCount;
}

void ULCPresentation::RemoveFlight(const FLCFlight& Item)
{
    if (!Item.Created) return;
    if (auto* Decal = Decals.Find(Item.Entity.Id)) { (*Decal)->DestroyComponent(); Decals.Remove(Item.Entity.Id); }
    else if (auto* Batch = Batches.Find(Item.BatchKey)) (*Batch)->RemoveInstanceById(Item.Instance);
}

void ULCPresentation::UpdateCamera(float DeltaTime)
{
    auto* Player = Host ? UGameplayStatics::GetPlayerController(Host->GetWorld(), 0) : nullptr;
    if (!Player) return;
    if (State == TEXT("previewing") || State == TEXT("settled"))
    {
        if (Player->WasInputKeyJustPressed(EKeys::Escape)) Skip();
        if (OwnsCamera && Director)
        {
            const APawn* Pawn = Player->GetPawn();
            const bool Moving = Pawn && (!Pawn->GetVelocity().IsNearlyZero(10) || !Pawn->GetPendingMovementInputVector().IsNearlyZero());
            const FRotator Control = Player->GetControlRotation();
            if (Moving || !Control.Equals(LastControlRotation, 0.05)) ReturnCamera();
            LastControlRotation = Control;
        }
    }
    if (OwnsCamera && HasCameraTarget)
    {
        Camera->SetActorLocation(FMath::VInterpTo(Camera->GetActorLocation(), CameraTargetLocation, DeltaTime, 5));
        Camera->SetActorRotation(FMath::RInterpTo(Camera->GetActorRotation(), CameraTargetRotation, DeltaTime, 5));
    }
}

void ULCPresentation::Tick(float DeltaTime)
{
    const double Now = FPlatformTime::Seconds();
    UpdateCamera(DeltaTime); UpdateOverlay();
    if (State == TEXT("previewing") || State == TEXT("settled") || State == TEXT("committed"))
    {
        FrameTotal += DeltaTime; ++FrameCount;
        MaxFrameMs = FMath::Max(MaxFrameMs, double(DeltaTime) * 1000);
    }
    if (State == TEXT("committed") && Now >= HeroUntil)
    { ReturnCamera(); MusicAudio->Stop(); State = TEXT("completed"); Ended = Now; Event(TEXT("presentation_complete")); }
    if (State != TEXT("previewing")) return;
    if (ShouldAutoSkip() && !Director)
    {
        const FString Reason = PlayerSkipReason();
        if (!Reason.IsEmpty()) Skip(Reason);
    }
    const double FrameStart = FPlatformTime::Seconds();
    for (auto& Pair : GrowthGroups)
    {
        auto& Group = Pair.Value;
        if (Group.Started < 0 && Group.Prepared == Group.Count)
            Group.Started = Now + Group.Delay;
        if (Group.Progress >= 1) continue;
        Group.Progress = Instant ? 1 : Group.Started < 0 ? 0 :
            FMath::Clamp((Now - Group.Started) / FMath::Max(0.001, Group.Duration), 0.0, 1.0);
        Group.Height = FMath::Lerp(Group.Bounds.Min.Z - 1, Group.Bounds.Max.Z + 1, Group.Progress);
        for (const auto& Key : Group.BatchKeys)
            if (auto* Batch = Batches.Find(Key))
                if (auto* Material = Cast<UMaterialInstanceDynamic>((*Batch)->GetMaterial(0)))
                    Material->SetScalarParameterValue(TEXT("LCRevealHeightCM"), Group.Height);
        for (const auto& Id : Group.DecalIds)
            if (auto* Decal = Decals.Find(Id))
                if (auto* Material = Cast<UMaterialInstanceDynamic>((*Decal)->GetDecalMaterial()))
                    Material->SetScalarParameterValue(TEXT("LCRevealHeightCM"), Group.Height);
        if (Group.Progress > 0 && Group.Progress < 1 && CurrentGroup != Pair.Key &&
            CameraEnabled && Cameras && Now - LastCameraCut >= 2)
        {
            const TSharedPtr<FJsonObject>* View;
            if (Cameras->GetObjectField(TEXT("groups"))->TryGetObjectField(Group.Name, View))
            { CurrentGroup = Pair.Key; Shot(*View, TEXT("module:") + Group.Name); Event(TEXT("module_camera")); }
        }
    }
    TArray<FBox> Boxes; TArray<double> Heights;
    bool RegionsDone = true;
    for (auto& Region : GrowthRegions)
    {
        Region.Progress = Instant ? 1 : FMath::Clamp((Now - Region.Started) / Region.Duration, 0.0, 1.0);
        RegionsDone &= Region.Progress >= 1;
        Boxes.Add(Region.Bounds);
        Heights.Add(FMath::Lerp(Region.Bounds.Min.Z - 1, Region.Bounds.Max.Z + 1, Region.Progress));
    }
    Wireframe->SetClippedBoxes(Boxes, Heights);
    int32 Visited = 0;
    while (Visited++ < Flights.Num() && FPlatformTime::Seconds() - FrameStart < 0.00085)
    {
        FLCFlight& Item = Flights[Cursor]; Cursor = (Cursor + 1) % Flights.Num();
        if (Item.Settled) continue;
        if (!Item.Animate) { Item.Settled = true; ++Settled; continue; }
        auto& Group = GrowthGroups[Item.GrowthKey];
        if (!Item.Created)
        {
            FString Error;
            if (Item.Entity.Template.StartsWith(TEXT("decal/")))
            {
                auto* Decal = LCCreateDecal(Host, Item.Entity, Error, true);
                if (!Decal) { Cancel(); return; }
                Decals.Add(Item.Entity.Id, Decal);
                CastChecked<UMaterialInstanceDynamic>(Decal->GetDecalMaterial())->SetScalarParameterValue(TEXT("LCRevealHeightCM"), Group.Height);
            }
            else
            {
                if (!Batches.Contains(Item.BatchKey))
                {
                    auto* Batch = LCCreateBatch(Host, Item.Entity, true, Error);
                    if (!Batch) { Cancel(); return; }
                    Batches.Add(Item.BatchKey, Batch);
                    CastChecked<UMaterialInstanceDynamic>(Batch->GetMaterial(0))->SetScalarParameterValue(TEXT("LCRevealHeightCM"), Group.Height);
                }
                Item.Instance = Batches[Item.BatchKey]->AddInstanceById(Item.Entity.Transform(), true);
            }
            Item.Created = true; ++Created; ++Group.Prepared;
        }
        if (Group.Progress >= 1)
        {
            Item.Settled = true; ++Settled;
            if (MusicWave)
            {
                const double Target = Group.Started + Group.Duration;
                const double ErrorMs = (Now - Target) * 1000;
                MaxBeatErrorMs = FMath::Max(MaxBeatErrorMs, FMath::Abs(ErrorMs));
                auto SettledEvent = Event(TEXT("flight_settled"));
                SettledEvent->SetStringField(TEXT("entity_id"), Item.Entity.Id);
                SettledEvent->SetNumberField(TEXT("target_s"), Target - Started);
                SettledEvent->SetNumberField(TEXT("actual_s"), Now - Started);
                SettledEvent->SetNumberField(TEXT("error_ms"), ErrorMs);
            }
        }
    }
    if (FirstVisible < 0 && (RegionCount > 0 || Created > 0))
    { FirstVisible = Now - Started; Event(TEXT("first_visible")); }
    if (Settled == Flights.Num() && RegionsDone)
    { State = TEXT("settled"); Event(TEXT("preview_settled")); }
    MaxTickMs = FMath::Max(MaxTickMs, (FPlatformTime::Seconds() - FrameStart) * 1000);
}

bool ULCPresentation::Matches(const TSharedPtr<FJsonObject>& Request, FString& Error) const
{
    FString RequestedSource; double RequestedRevision;
    const TArray<TSharedPtr<FJsonValue>>* Entities;
    if (!Request->TryGetStringField(TEXT("source"), RequestedSource) || RequestedSource != Source ||
        !Request->TryGetNumberField(TEXT("revision"), RequestedRevision) || RequestedRevision != Revision ||
        !(State == TEXT("previewing") || State == TEXT("settled")) ||
        !Request->TryGetArrayField(TEXT("entities"), Entities) || Entities->Num() != Flights.Num())
    { Error = TEXT("Commit must match the active preview source and revision"); return false; }
    for (int32 Index = 0; Index < Flights.Num(); ++Index)
    {
        FLCEntity Entity;
        if ((*Entities)[Index]->Type != EJson::Object || !FLCEntity::FromJson((*Entities)[Index]->AsObject(), Entity, Error)) return false;
        Entity.Source = Source; Entity.Version = Flights[Index].Entity.Version;
        if (LCJsonString(Entity.ToJson()) != LCJsonString(Flights[Index].Entity.ToJson()))
        { Error = TEXT("Commit entity content differs from the published preview"); return false; }
    }
    return true;
}

TSharedPtr<FJsonObject> ULCPresentation::Status() const
{
    auto Json = MakeShared<FJsonObject>();
    Json->SetStringField(TEXT("effect"), TEXT("upward"));
    Json->SetStringField(TEXT("caption"), Caption);
    Json->SetBoolField(TEXT("director"), Director);
    Json->SetNumberField(TEXT("geometry_transform_updates"), 0);
    Json->SetNumberField(TEXT("handoff_count"), HandoffCount);
    TArray<TSharedPtr<FJsonValue>> Groups;
    TArray<FString> Keys; GrowthGroups.GetKeys(Keys); Keys.Sort();
    for (const auto& Key : Keys)
    {
        const auto& Group = GrowthGroups[Key];
        auto Value = MakeShared<FJsonObject>();
        Value->SetStringField(TEXT("id"), Group.Name);
        Value->SetNumberField(TEXT("progress"), Group.Progress);
        Value->SetNumberField(TEXT("height_cm"), Group.Height);
        Value->SetNumberField(TEXT("min_z_cm"), Group.Bounds.Min.Z);
        Value->SetNumberField(TEXT("max_z_cm"), Group.Bounds.Max.Z);
        Value->SetNumberField(TEXT("instance_count"), Group.Count);
        Value->SetNumberField(TEXT("prepared_count"), Group.Prepared);
        Groups.Add(MakeShared<FJsonValueObject>(Value));
    }
    Json->SetArrayField(TEXT("growth_groups"), Groups);
    Json->SetBoolField(TEXT("instant"), Instant);
    Json->SetBoolField(TEXT("auto_skip"), AutoSkip);
    Json->SetNumberField(TEXT("animated_count"), AnimatedCount);
    Json->SetStringField(TEXT("skip_reason"), SkipReason);
    Json->SetStringField(TEXT("source"), Source); Json->SetNumberField(TEXT("revision"), Revision);
    Json->SetStringField(TEXT("state"), State); Json->SetStringField(TEXT("camera"), CameraStage);
    Json->SetNumberField(TEXT("preview_count"), Created); Json->SetNumberField(TEXT("region_count"), RegionCount);
    Json->SetNumberField(TEXT("settled_count"), Settled); Json->SetNumberField(TEXT("desired_count"), Flights.Num());
    Json->SetNumberField(TEXT("first_visible_game_frame_s"), FirstVisible);
    Json->SetNumberField(TEXT("elapsed_s"), Started > 0 ? (Ended > 0 ? Ended : FPlatformTime::Seconds()) - Started : 0);
    Json->SetNumberField(TEXT("max_tick_ms"), MaxTickMs); Json->SetNumberField(TEXT("max_frame_ms"), MaxFrameMs);
    Json->SetNumberField(TEXT("frame_count"), FrameCount);
    Json->SetNumberField(TEXT("average_fps"), FrameTotal > 0 ? FrameCount / FrameTotal : 0);
    Json->SetNumberField(TEXT("min_fps"), MaxFrameMs > 0 ? 1000.0 / MaxFrameMs : 0);
    Json->SetStringField(TEXT("music"), Music);
    Json->SetNumberField(TEXT("audio_clock_started_s"), MusicWave ? MusicWave->RenderStarted() : 0);
    Json->SetNumberField(TEXT("max_beat_error_ms"), MaxBeatErrorMs);
    Json->SetNumberField(TEXT("audio_output_latency_ms"), AudioOutputLatencyMs);
    return Json;
}

TSharedPtr<FJsonObject> ULCPresentation::Events(int64 Since) const
{
    auto Json = MakeShared<FJsonObject>(); TArray<TSharedPtr<FJsonValue>> Values;
    const int64 Oldest = EventLog.IsEmpty() ? Sequence + 1 : static_cast<int64>(EventLog[0]->AsObject()->GetNumberField(TEXT("seq")));
    for (const auto& Value : EventLog) if (Value->AsObject()->GetNumberField(TEXT("seq")) > Since) Values.Add(Value);
    Json->SetArrayField(TEXT("events"), Values); Json->SetStringField(TEXT("session_id"), Session);
    Json->SetNumberField(TEXT("next"), Sequence); Json->SetNumberField(TEXT("oldest"), Oldest);
    Json->SetBoolField(TEXT("truncated"), Since < Oldest - 1);
    return Json;
}
