#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Character.h"
#include "LCCharacter.generated.h"

class UInputAction;
class UInputMappingContext;
struct FInputActionValue;

UCLASS()
class PROJECTLC_API ALCCharacter : public ACharacter
{
    GENERATED_BODY()

public:
    ALCCharacter();
    virtual void PawnClientRestart() override;
    virtual void SetupPlayerInputComponent(UInputComponent* Input) override;

private:
    void Move(const FInputActionValue& Value);
    void Look(const FInputActionValue& Value);

    UPROPERTY() TObjectPtr<UInputMappingContext> MovementMapping;
    UPROPERTY() TObjectPtr<UInputMappingContext> MouseMapping;
    UPROPERTY() TObjectPtr<UInputAction> MoveAction;
    UPROPERTY() TObjectPtr<UInputAction> LookAction;
    UPROPERTY() TObjectPtr<UInputAction> MouseLookAction;
    UPROPERTY() TObjectPtr<UInputAction> JumpAction;
};
