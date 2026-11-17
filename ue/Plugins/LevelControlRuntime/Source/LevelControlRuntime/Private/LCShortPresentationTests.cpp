#include "LCTxService.h"
#include "LCSubsystem.h"
#include "Engine/Engine.h"
#include "Engine/GameViewportClient.h"
#include "Engine/World.h"
#include "GameFramework/Character.h"
#include "GameFramework/CharacterMovementComponent.h"
#include "Kismet/GameplayStatics.h"
#include "Misc/AutomationTest.h"

#if WITH_DEV_AUTOMATION_TESTS
IMPLEMENT_SIMPLE_AUTOMATION_TEST(FLCShortPresentationGuards, "LC.Presentation.PlayerGuards",
    EAutomationTestFlags::ClientContext | EAutomationTestFlags::EngineFilter)

bool FLCShortPresentationGuards::RunTest(const FString& Parameters)
{
    auto* World = GEngine->GameViewport ? GEngine->GameViewport->GetWorld() : nullptr;
    auto* Player = World ? UGameplayStatics::GetPlayerCharacter(World, 0) : nullptr;
    if (!Player) { AddError(TEXT("Run in an isolated -game world")); return false; }
    auto* Service = World->GetSubsystem<ULCSubsystem>()->GetTransactions();
    const FVector OriginalVelocity = Player->GetCharacterMovement()->Velocity;
    auto Cancel = [&] {
        auto Request = MakeShared<FJsonObject>();
        Request->SetStringField(TEXT("req_id"), FGuid::NewGuid().ToString(EGuidFormats::Digits));
        Request->SetStringField(TEXT("action"), TEXT("cancel"));
        Service->Control(Request);
    };
    auto Publish = [&](bool Inside) {
        auto Request = MakeShared<FJsonObject>();
        Request->SetStringField(TEXT("req_id"), FGuid::NewGuid().ToString(EGuidFormats::Digits));
        Request->SetStringField(TEXT("source"), TEXT("player-guard"));
        Request->SetNumberField(TEXT("revision"), Service->PresentationStatus()->GetNumberField(TEXT("revision")) + 1);
        Request->SetBoolField(TEXT("auto_skip"), true);
        Request->SetNumberField(TEXT("finish_delay_s"), 0);
        FLCEntity Entity; Entity.Id = TEXT("guard-box"); Entity.Template = TEXT("blockout/box");
        Entity.Position = Player->GetActorLocation() + FVector(800, 0, 0); Entity.Size = FVector(100);
        Request->SetArrayField(TEXT("entities"), {MakeShared<FJsonValueObject>(Entity.ToJson())});
        auto Region = MakeShared<FJsonObject>();
        Region->SetArrayField(TEXT("center_cm"), LCVector(Inside ? Player->GetActorLocation() : Entity.Position));
        Region->SetArrayField(TEXT("size_cm"), LCVector(FVector(300)));
        Request->SetArrayField(TEXT("regions"), {MakeShared<FJsonValueObject>(Region)});
        Request->SetArrayField(TEXT("timeline"), {MakeShared<FJsonValueObject>(LCParseJson(
            TEXT(R"({"id":"guard-box","start_s":0,"duration_s":4})")))});
        return Service->Preview(Request);
    };
    Cancel();
    Player->GetCharacterMovement()->Velocity = FVector(100, 0, 0);
    TestEqual(TEXT("Moving preview accepted"), Publish(false)->GetStringField(TEXT("status")), FString(TEXT("applied")));
    TestEqual(TEXT("Movement skips reveal"), Service->PresentationStatus()->GetStringField(TEXT("skip_reason")), FString(TEXT("player_moving")));
    Cancel();
    Player->GetCharacterMovement()->Velocity = FVector::ZeroVector;
    Player->ConsumeMovementInputVector();
    TestEqual(TEXT("Occupied preview accepted"), Publish(true)->GetStringField(TEXT("status")), FString(TEXT("applied")));
    TestEqual(TEXT("Occupied area skips reveal"), Service->PresentationStatus()->GetStringField(TEXT("skip_reason")), FString(TEXT("player_inside_area")));
    Cancel();
    Player->GetCharacterMovement()->Velocity = OriginalVelocity;
    return true;
}
#endif
