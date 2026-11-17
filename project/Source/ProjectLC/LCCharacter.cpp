#include "LCCharacter.h"
#include "Animation/AnimInstance.h"
#include "Camera/CameraComponent.h"
#include "Components/CapsuleComponent.h"
#include "Components/SkeletalMeshComponent.h"
#include "Engine/LocalPlayer.h"
#include "EnhancedInputComponent.h"
#include "EnhancedInputSubsystems.h"
#include "GameFramework/CharacterMovementComponent.h"
#include "GameFramework/PlayerController.h"
#include "GameFramework/SpringArmComponent.h"
#include "InputAction.h"
#include "InputActionValue.h"
#include "InputMappingContext.h"
#include "UObject/ConstructorHelpers.h"

ALCCharacter::ALCCharacter()
{
    GetCapsuleComponent()->InitCapsuleSize(42.f, 96.f);
    bUseControllerRotationPitch = false;
    bUseControllerRotationYaw = false;
    bUseControllerRotationRoll = false;
    auto* Movement = GetCharacterMovement();
    Movement->bOrientRotationToMovement = true;
    Movement->RotationRate = FRotator(0, 500, 0);
    Movement->JumpZVelocity = 500.f;
    Movement->AirControl = 0.35f;
    Movement->MaxWalkSpeed = 500.f;
    Movement->BrakingDecelerationWalking = 2000.f;
    Movement->BrakingDecelerationFalling = 1500.f;

    auto* Boom = CreateDefaultSubobject<USpringArmComponent>(TEXT("CameraBoom"));
    Boom->SetupAttachment(RootComponent);
    Boom->TargetArmLength = 450.f;
    Boom->SocketOffset = FVector(0, 60, 70);
    Boom->bUsePawnControlRotation = true;
    auto* Camera = CreateDefaultSubobject<UCameraComponent>(TEXT("FollowCamera"));
    Camera->SetupAttachment(Boom, USpringArmComponent::SocketName);

    static ConstructorHelpers::FObjectFinder<USkeletalMesh> CharacterMesh(
        TEXT("/Game/Characters/Mannequins/Meshes/SKM_Manny_Simple"));
    static ConstructorHelpers::FClassFinder<UAnimInstance> Animation(
        TEXT("/Game/Characters/Mannequins/Anims/Unarmed/ABP_Unarmed"));
    GetMesh()->SetSkeletalMesh(CharacterMesh.Object);
    GetMesh()->SetRelativeLocation(FVector(0, 0, -96));
    GetMesh()->SetRelativeRotation(FRotator(0, -90, 0));
    GetMesh()->SetAnimInstanceClass(Animation.Class);
    GetMesh()->SetCollisionEnabled(ECollisionEnabled::NoCollision);

    static ConstructorHelpers::FObjectFinder<UInputMappingContext> Mapping(TEXT("/Game/Input/IMC_Default"));
    static ConstructorHelpers::FObjectFinder<UInputMappingContext> Mouse(TEXT("/Game/Input/IMC_MouseLook"));
    static ConstructorHelpers::FObjectFinder<UInputAction> MoveInput(TEXT("/Game/Input/Actions/IA_Move"));
    static ConstructorHelpers::FObjectFinder<UInputAction> LookInput(TEXT("/Game/Input/Actions/IA_Look"));
    static ConstructorHelpers::FObjectFinder<UInputAction> MouseInput(TEXT("/Game/Input/Actions/IA_MouseLook"));
    static ConstructorHelpers::FObjectFinder<UInputAction> JumpInput(TEXT("/Game/Input/Actions/IA_Jump"));
    MovementMapping = Mapping.Object;
    MouseMapping = Mouse.Object;
    MoveAction = MoveInput.Object;
    LookAction = LookInput.Object;
    MouseLookAction = MouseInput.Object;
    JumpAction = JumpInput.Object;
}

void ALCCharacter::PawnClientRestart()
{
    Super::PawnClientRestart();
    if (const auto* Player = Cast<APlayerController>(GetController()))
    {
        if (auto* LocalPlayer = Player->GetLocalPlayer())
        {
            auto* Input = ULocalPlayer::GetSubsystem<UEnhancedInputLocalPlayerSubsystem>(LocalPlayer);
            Input->AddMappingContext(MovementMapping, 0);
            Input->AddMappingContext(MouseMapping, 0);
        }
    }
}

void ALCCharacter::SetupPlayerInputComponent(UInputComponent* Input)
{
    Super::SetupPlayerInputComponent(Input);
    auto* Enhanced = CastChecked<UEnhancedInputComponent>(Input);
    Enhanced->BindAction(MoveAction, ETriggerEvent::Triggered, this, &ALCCharacter::Move);
    Enhanced->BindAction(LookAction, ETriggerEvent::Triggered, this, &ALCCharacter::Look);
    Enhanced->BindAction(MouseLookAction, ETriggerEvent::Triggered, this, &ALCCharacter::Look);
    Enhanced->BindAction(JumpAction, ETriggerEvent::Started, this, &ACharacter::Jump);
    Enhanced->BindAction(JumpAction, ETriggerEvent::Completed, this, &ACharacter::StopJumping);
}

void ALCCharacter::Move(const FInputActionValue& Value)
{
    const FVector2D Axis = Value.Get<FVector2D>();
    const FRotator Yaw(0, GetControlRotation().Yaw, 0);
    AddMovementInput(Yaw.Vector(), Axis.Y);
    AddMovementInput(FRotationMatrix(Yaw).GetUnitAxis(EAxis::Y), Axis.X);
}

void ALCCharacter::Look(const FInputActionValue& Value)
{
    const FVector2D Axis = Value.Get<FVector2D>();
    AddControllerYawInput(Axis.X);
    AddControllerPitchInput(Axis.Y);
}
