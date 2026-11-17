#include "LCCharacter.h"
#include "Camera/CameraComponent.h"
#include "Components/SkeletalMeshComponent.h"
#include "Engine/Engine.h"
#include "Engine/GameViewportClient.h"
#include "GameFramework/CharacterMovementComponent.h"
#include "Kismet/GameplayStatics.h"
#include "Misc/AutomationTest.h"

#if WITH_DEV_AUTOMATION_TESTS
class FLCMovementSmoke : public IAutomationLatentCommand
{
public:
    explicit FLCMovementSmoke(FAutomationTestBase* InTest) : Test(InTest) {}

    virtual bool Update() override
    {
        if (!Character.IsValid())
        {
            UWorld* World = GEngine->GameViewport ? GEngine->GameViewport->GetWorld() : nullptr;
            Character = World ? Cast<ALCCharacter>(UGameplayStatics::GetPlayerCharacter(World, 0)) : nullptr;
            if (!Character.IsValid())
            {
                Test->AddError(TEXT("No runtime LC character; run this test with -game."));
                return true;
            }
            Origin = Character->GetActorLocation();
            Started = FPlatformTime::Seconds();
            PeakZ = Origin.Z;
            Test->TestNotNull(TEXT("Manny mesh loaded"), Character->GetMesh()->GetSkeletalMeshAsset());
            Test->TestNotNull(TEXT("Animation loaded"), Character->GetMesh()->GetAnimInstance());
            Test->TestNotNull(TEXT("Third person camera"), Character->FindComponentByClass<UCameraComponent>());
        }
        const double Elapsed = FPlatformTime::Seconds() - Started;
        if (Elapsed < 0.6) Character->AddMovementInput(FVector::ForwardVector);
        if (Elapsed > 0.3 && !bJumped) { Character->Jump(); bJumped = true; }
        if (Elapsed > 0.6) Character->StopJumping();
        PeakZ = FMath::Max(PeakZ, Character->GetActorLocation().Z);
        if (Elapsed < 3.0) return false;
        const double Distance = FVector::Dist2D(Origin, Character->GetActorLocation());
        Test->TestTrue(TEXT("Moved at least 100 cm"), Distance > 100.0);
        Test->TestTrue(TEXT("Jumped at least 80 cm"), PeakZ - Origin.Z > 80.0);
        Test->TestTrue(TEXT("Landed on floor"), Character->GetCharacterMovement()->IsMovingOnGround());
        Test->AddInfo(FString::Printf(TEXT("Movement %.1f cm; jump %.1f cm; landed=%d"),
            Distance, PeakZ - Origin.Z, Character->GetCharacterMovement()->IsMovingOnGround()));
        FScreenshotRequest::RequestScreenshot(TEXT("step1-runtime.png"), false, false);
        return true;
    }

private:
    FAutomationTestBase* Test;
    TWeakObjectPtr<ALCCharacter> Character;
    FVector Origin = FVector::ZeroVector;
    double Started = 0;
    double PeakZ = 0;
    bool bJumped = false;
};

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FLCGameSmokeTest, "ProjectLC.Step1.MovementAndJump",
    EAutomationTestFlags::ClientContext | EAutomationTestFlags::EngineFilter)

bool FLCGameSmokeTest::RunTest(const FString& Parameters)
{
    ADD_LATENT_AUTOMATION_COMMAND(FLCMovementSmoke(this));
    return true;
}
#endif
