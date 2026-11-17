#include "LCTxService.h"
#include "Engine/World.h"
#include "GameFramework/Character.h"
#include "GameFramework/CharacterMovementComponent.h"
#include "Components/CapsuleComponent.h"
#include "Kismet/GameplayStatics.h"

FString ULCTxService::HitId(const FHitResult& Hit) const
{
    for (const auto& Item : Instances)
        if (Batches[Item.Value.Template] == Hit.GetComponent() &&
            Batches[Item.Value.Template]->GetInstanceIndexForId(Item.Value.Handle) == Hit.Item) return Item.Key;
    return TEXT("world");
}

TSharedPtr<FJsonObject> ULCTxService::Check(const TSharedPtr<FJsonObject>& Request)
{
    auto Result = MakeShared<FJsonObject>();
    auto Reject = [&](const FString& Error) { Result->SetBoolField(TEXT("ok"), false); Result->SetStringField(TEXT("error"), Error); return Result; };
    auto* Player = UGameplayStatics::GetPlayerCharacter(Host->GetWorld(), 0);
    if (!Request || !Player || IsBusy()) return Reject(TEXT("No player or entity transaction is busy"));
    FString Kind;
    if (!Request->TryGetStringField(TEXT("kind"), Kind)) return Reject(TEXT("kind is required"));
    auto* Movement = Player->GetCharacterMovement();
    double Radius = Player->GetCapsuleComponent()->GetScaledCapsuleRadius();
    double HalfHeight = Player->GetCapsuleComponent()->GetScaledCapsuleHalfHeight();
    FCollisionQueryParams Query(SCENE_QUERY_STAT(LCCheck), false, Player);
    FHitResult Hit;
    if (Kind == TEXT("sightline"))
    {
        FVector From = FVector::ZeroVector, To = FVector::ZeroVector;
        if (DesignPreview || !LCReadVector(Request, TEXT("from_cm"), From) || !LCReadVector(Request, TEXT("to_cm"), To) || From.Equals(To, 0.0001))
            return Reject(TEXT("Sightline requires distinct finite points and no geometry preview"));
        const bool Blocked = Host->GetWorld()->LineTraceSingleByChannel(Hit, From, To, ECC_Visibility, Query);
        Result->SetBoolField(TEXT("blocked"), Blocked);
        Result->SetStringField(TEXT("entity_id"), Blocked ? HitId(Hit) : TEXT(""));
        Result->SetNumberField(TEXT("distance_cm"), Blocked ? Hit.Distance : FVector::Distance(From, To));
        Result->SetArrayField(TEXT("hit_cm"), LCVector(Blocked ? Hit.ImpactPoint : To));
    }
    else if (Kind == TEXT("clearance"))
    {
        FVector From = FVector::ZeroVector, To = FVector::ZeroVector;
        Request->TryGetNumberField(TEXT("radius_cm"), Radius);
        Request->TryGetNumberField(TEXT("half_height_cm"), HalfHeight);
        if (!LCReadVector(Request, TEXT("from_cm"), From) || !LCReadVector(Request, TEXT("to_cm"), To) ||
            !FMath::IsFinite(Radius) || !FMath::IsFinite(HalfHeight) || Radius < 1 || Radius > 500 || HalfHeight < Radius || HalfHeight > 1000)
            return Reject(TEXT("Invalid clearance coordinates or capsule"));
        From.Z += HalfHeight + 1; To.Z += HalfHeight + 1;
        const bool Blocked = Host->GetWorld()->SweepSingleByChannel(Hit, From, To, FQuat::Identity, ECC_Pawn,
            FCollisionShape::MakeCapsule(Radius, HalfHeight), Query);
        Result->SetBoolField(TEXT("blocked"), Blocked);
        if (Blocked) { Result->SetStringField(TEXT("entity_id"), HitId(Hit)); Result->SetArrayField(TEXT("hit_cm"), LCVector(Hit.ImpactPoint)); }
    }
    else if (Kind == TEXT("support"))
    {
        FVector Point;
        if (!LCReadVector(Request, TEXT("point_cm"), Point)) return Reject(TEXT("point_cm is required"));
        const bool Found = Host->GetWorld()->LineTraceSingleByChannel(Hit, Point + FVector(0, 0, 50), Point - FVector(0, 0, 500), ECC_Pawn, Query);
        Result->SetBoolField(TEXT("supported"), Found && Hit.ImpactNormal.Z >= Movement->GetWalkableFloorZ());
        if (Found) { Result->SetArrayField(TEXT("point_cm"), LCVector(Hit.ImpactPoint)); Result->SetStringField(TEXT("entity_id"), HitId(Hit)); }
    }
    else if (Kind == TEXT("jump"))
    {
        FVector Start;
        double Heading = 0, Speed = Movement->MaxWalkSpeed, MaximumTime = 3;
        Request->TryGetNumberField(TEXT("heading_deg"), Heading);
        Request->TryGetNumberField(TEXT("speed_cm_s"), Speed);
        Request->TryGetNumberField(TEXT("max_time_s"), MaximumTime);
        if (!LCReadVector(Request, TEXT("start_cm"), Start) || !FMath::IsFinite(Heading) ||
            !FMath::IsFinite(Speed) || Speed < 0 || Speed > Movement->MaxWalkSpeed ||
            !FMath::IsFinite(MaximumTime) || MaximumTime < 0.1 || MaximumTime > 5)
            return Reject(TEXT("Invalid jump start, heading, speed or duration"));
        const FVector Velocity = FRotator(0, Heading, 0).Vector() * Speed + FVector(0, 0, Movement->JumpZVelocity);
        const double Gravity = Movement->GetGravityZ();
        FVector Previous = Start + FVector(0, 0, HalfHeight + 1);
        TArray<TSharedPtr<FJsonValue>> Path;
        Path.Add(MakeShared<FJsonValueArray>(LCVector(Start)));
        bool Blocked = false, Landed = false;
        double HitTime = 0;
        FVector Landing = Start;
        constexpr double Step = 1.0 / 120.0;
        for (double Time = Step; Time <= MaximumTime + Step / 2; Time += Step)
        {
            const FVector Next = Start + FVector(0, 0, HalfHeight + 1) + Velocity * Time + FVector(0, 0, 0.5 * Gravity * Time * Time);
            Blocked = Host->GetWorld()->SweepSingleByChannel(Hit, Previous, Next, FQuat::Identity, ECC_Pawn,
                FCollisionShape::MakeCapsule(Radius, HalfHeight), Query);
            Landing = (Blocked ? Hit.Location : Next) - FVector(0, 0, HalfHeight);
            Path.Add(MakeShared<FJsonValueArray>(LCVector(Landing)));
            if (Blocked)
            {
                HitTime = Time - Step + Step * Hit.Time;
                Landed = Velocity.Z + Gravity * HitTime < 0 && Hit.ImpactNormal.Z >= Movement->GetWalkableFloorZ();
                break;
            }
            Previous = Next;
        }
        Result->SetBoolField(TEXT("landed"), Landed); Result->SetBoolField(TEXT("blocked"), Blocked);
        Result->SetArrayField(TEXT("path_cm"), Path); Result->SetArrayField(TEXT("landing_cm"), LCVector(Landing));
        Result->SetArrayField(TEXT("velocity_cm_s"), LCVector(Velocity));
        Result->SetNumberField(TEXT("gravity_cm_s2"), Gravity); Result->SetNumberField(TEXT("time_s"), HitTime);
        Result->SetNumberField(TEXT("horizontal_distance_cm"), FVector::Dist2D(Start, Landing));
        if (Blocked) Result->SetStringField(TEXT("entity_id"), HitId(Hit));
    }
    else return Reject(TEXT("Use kind jump, clearance or support"));
    Result->SetBoolField(TEXT("ok"), true); Result->SetStringField(TEXT("kind"), Kind);
    return Result;
}

TSharedPtr<FJsonObject> ULCTxService::PlayerControl(const TSharedPtr<FJsonObject>& Request)
{
    FString Id, Action, Error;
    if (!Request || !Request->TryGetStringField(TEXT("req_id"), Id) || Id.IsEmpty() || Id.Len() > 128)
        return MakeReceipt(TEXT(""), TEXT("rejected"), TEXT("req_id is required"));
    if (Receipts.Contains(Id)) return Receipts[Id];
    auto* Player = UGameplayStatics::GetPlayerCharacter(Host->GetWorld(), 0);
    if (!Player || IsBusy()) Error = TEXT("Player unavailable or entity transaction is busy");
    else if (!Request->TryGetStringField(TEXT("action"), Action)) Error = TEXT("action is required");
    else if (Action == TEXT("teleport"))
    {
        FVector Feet;
        if (!LCReadVector(Request, TEXT("feet_cm"), Feet)) Error = TEXT("feet_cm is required");
        else
        {
            const auto* Capsule = Player->GetCapsuleComponent();
            const FVector Center = Feet + FVector(0, 0, Capsule->GetScaledCapsuleHalfHeight() + 1);
            FCollisionQueryParams Query(SCENE_QUERY_STAT(LCPlayer), false, Player);
            if (Host->GetWorld()->OverlapBlockingTestByChannel(Center, FQuat::Identity, ECC_Pawn,
                FCollisionShape::MakeCapsule(Capsule->GetScaledCapsuleRadius(), Capsule->GetScaledCapsuleHalfHeight()), Query))
                Error = TEXT("Teleport capsule overlaps blocking geometry");
            else { Player->SetActorLocation(Center, false, nullptr, ETeleportType::TeleportPhysics); Player->GetCharacterMovement()->StopMovementImmediately(); }
        }
    }
    else if (Action == TEXT("configure"))
    {
        auto* Movement = Player->GetCharacterMovement();
        double Speed = Movement->MaxWalkSpeed, Jump = Movement->JumpZVelocity, Gravity = Movement->GravityScale;
        bool Supplied = false;
        for (const auto& Field : Request->Values)
        {
            const FString Key(Field.Key);
            if (Key == TEXT("req_id") || Key == TEXT("command") || Key == TEXT("action")) continue;
            double* Value = Key == TEXT("max_walk_speed_cm_s") ? &Speed : Key == TEXT("jump_velocity_cm_s") ? &Jump : Key == TEXT("gravity_scale") ? &Gravity : nullptr;
            if (!Value || !Field.Value->TryGetNumber(*Value) || !FMath::IsFinite(*Value)) { Error = TEXT("Unknown or nonnumeric player parameter"); break; }
            Supplied = true;
        }
        if (!Supplied || Speed < 1 || Speed > 2000 || Jump < 100 || Jump > 2000 || Gravity < 0.1 || Gravity > 5)
            Error = TEXT("Player parameter outside supported range");
        if (Error.IsEmpty()) { Movement->MaxWalkSpeed = Speed; Movement->JumpZVelocity = Jump; Movement->GravityScale = Gravity; }
    }
    else Error = TEXT("Use player action teleport or configure");
    auto Result = MakeReceipt(Id, Error.IsEmpty() ? TEXT("applied") : TEXT("rejected"), Error);
    Result->SetStringField(TEXT("tx_id"), TEXT("player-") + FGuid::NewGuid().ToString(EGuidFormats::Digits));
    Receipts.Add(Id, Result); return Result;
}
