#pragma once
#include "CoreMinimal.h"
#include "Components/PrimitiveComponent.h"
#include "LCWireframeComponent.generated.h"

UCLASS()
class LEVELCONTROLRUNTIME_API ULCWireframeComponent : public UPrimitiveComponent
{
    GENERATED_BODY()
public:
    ULCWireframeComponent();
    void SetBoxes(const TArray<FBox>& InBoxes);
    void SetClippedBoxes(const TArray<FBox>& InBoxes, const TArray<double>& Heights);
    virtual FPrimitiveSceneProxy* CreateSceneProxy() override;
    virtual FBoxSphereBounds CalcBounds(const FTransform& LocalToWorld) const override;
    const TArray<FBox>& GetBoxes() const { return Boxes; }
    const TArray<double>& GetHeights() const { return ClipHeights; }
private:
    TArray<FBox> Boxes;
    TArray<double> ClipHeights;
};
