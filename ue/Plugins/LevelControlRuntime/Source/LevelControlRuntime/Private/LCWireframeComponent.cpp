#include "LCWireframeComponent.h"
#include "PrimitiveSceneProxy.h"
#include "SceneManagement.h"

class FLCWireframeProxy final : public FPrimitiveSceneProxy
{
public:
    explicit FLCWireframeProxy(const ULCWireframeComponent* Component)
        : FPrimitiveSceneProxy(Component), Boxes(Component->GetBoxes()), Heights(Component->GetHeights()) {}
    virtual SIZE_T GetTypeHash() const override { static size_t Unique; return reinterpret_cast<SIZE_T>(&Unique); }
    virtual uint32 GetMemoryFootprint() const override { return sizeof(*this) + Boxes.GetAllocatedSize(); }
    virtual FPrimitiveViewRelevance GetViewRelevance(const FSceneView* View) const override
    {
        FPrimitiveViewRelevance Relevance;
        Relevance.bDrawRelevance = IsShown(View);
        Relevance.bDynamicRelevance = true;
        Relevance.bShadowRelevance = false;
        return Relevance;
    }
    virtual void GetDynamicMeshElements(const TArray<const FSceneView*>& Views, const FSceneViewFamily& ViewFamily,
        uint32 VisibilityMap, FMeshElementCollector& Collector) const override
    {
        for (int32 ViewIndex = 0; ViewIndex < Views.Num(); ++ViewIndex)
        {
            if (!(VisibilityMap & (1u << ViewIndex))) continue;
            auto* Draw = Collector.GetPDI(ViewIndex);
            for (int32 BoxIndex = 0; BoxIndex < Boxes.Num(); ++BoxIndex)
            {
                const auto& Box = Boxes[BoxIndex];
                const double Height = Heights.IsValidIndex(BoxIndex) ? Heights[BoxIndex] : 100000000;
                FVector Corners[8];
                for (int32 Index = 0; Index < 8; ++Index)
                    Corners[Index] = FVector(Index & 1 ? Box.Max.X : Box.Min.X, Index & 2 ? Box.Max.Y : Box.Min.Y, Index & 4 ? Box.Max.Z : Box.Min.Z);
                for (int32 Index = 0; Index < 8; ++Index)
                    for (int32 Axis = 0; Axis < 3; ++Axis)
                        if (!(Index & (1 << Axis)))
                        {
                            FVector A = Corners[Index], B = Corners[Index | (1 << Axis)];
                            if (A.Z > Height && B.Z > Height) continue;
                            if (A.Z > Height) A = FMath::Lerp(B, A, (Height - B.Z) / (A.Z - B.Z));
                            if (B.Z > Height) B = FMath::Lerp(A, B, (Height - A.Z) / (B.Z - A.Z));
                            Draw->DrawLine(A, B, FLinearColor(0, 1, 1), SDPG_World, 2.0f, 0, true);
                        }
            }
        }
    }
private:
    TArray<FBox> Boxes;
    TArray<double> Heights;
};

ULCWireframeComponent::ULCWireframeComponent()
{
    SetCollisionEnabled(ECollisionEnabled::NoCollision);
    CastShadow = false;
    bUseAsOccluder = false;
}

void ULCWireframeComponent::SetBoxes(const TArray<FBox>& InBoxes)
{
    SetClippedBoxes(InBoxes, {});
}

void ULCWireframeComponent::SetClippedBoxes(const TArray<FBox>& InBoxes, const TArray<double>& Heights)
{
    if (Boxes == InBoxes && ClipHeights == Heights) return;
    Boxes = InBoxes; ClipHeights = Heights;
    UpdateBounds();
    MarkRenderStateDirty();
}

FPrimitiveSceneProxy* ULCWireframeComponent::CreateSceneProxy() { return new FLCWireframeProxy(this); }

FBoxSphereBounds ULCWireframeComponent::CalcBounds(const FTransform& LocalToWorld) const
{
    FBox Union(ForceInit);
    for (const auto& Box : Boxes) Union += Box;
    return Boxes.IsEmpty() ? FBoxSphereBounds(FVector::ZeroVector, FVector(1), 1) : FBoxSphereBounds(Union);
}
