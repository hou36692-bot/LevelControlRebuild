#pragma once
#include "CoreMinimal.h"
#include "Components/ActorComponent.h"
#include "LCIdentityComponent.generated.h"

class UInstancedStaticMeshComponent;

USTRUCT(BlueprintType)
struct FLCInstanceIdentity
{
    GENERATED_BODY()
    UPROPERTY(EditAnywhere, BlueprintReadWrite) TObjectPtr<UInstancedStaticMeshComponent> Component;
    UPROPERTY(EditAnywhere, BlueprintReadWrite) int32 InitialIndex = 0;
    UPROPERTY(EditAnywhere, BlueprintReadWrite) FString PersistentId;
};

/** Author-supplied identity, cooked with the actor. Never derived from an actor name. */
UCLASS(ClassGroup=(LevelControl), meta=(BlueprintSpawnableComponent))
class LEVELCONTROLRUNTIME_API ULCIdentityComponent : public UActorComponent
{
    GENERATED_BODY()
public:
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category="Identity") FString PersistentId;
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category="Identity") FString DisplayName;
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category="Identity") FString Role;
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category="Identity") TArray<FString> Aliases;
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category="Identity") FString Area;
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category="Identity") TArray<FLCInstanceIdentity> Instances;
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category="Identity") bool ExcludeFromRegistry = false;
    UPROPERTY(BlueprintReadWrite, Category="State") TMap<FString, FString> State;
};
