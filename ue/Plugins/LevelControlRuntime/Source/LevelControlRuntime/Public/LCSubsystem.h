#pragma once
#include "CoreMinimal.h"
#include "Subsystems/WorldSubsystem.h"
#include "HttpRouteHandle.h"
#include "LCSubsystem.generated.h"

class IHttpRouter;
class ULCTxService;
struct FHttpServerRequest;
class FJsonObject;

UCLASS()
class LEVELCONTROLRUNTIME_API ULCSubsystem : public UTickableWorldSubsystem
{
    GENERATED_BODY()
public:
    virtual bool ShouldCreateSubsystem(UObject* Outer) const override;
    virtual void OnWorldBeginPlay(UWorld& World) override;
    virtual void Deinitialize() override;
    virtual void Tick(float DeltaTime) override;
    virtual TStatId GetStatId() const override { RETURN_QUICK_DECLARE_CYCLE_STAT(ULCSubsystem, STATGROUP_Tickables); }
    ULCTxService* GetTransactions() const { return Transactions; }

private:
    TSharedPtr<FJsonObject> Handle(const FString& Path, const FHttpServerRequest& Request);
    int32 Port = 18844;
    TSharedPtr<IHttpRouter> Router;
    TArray<FHttpRouteHandle> Routes;
    UPROPERTY() TObjectPtr<ULCTxService> Transactions;
};
