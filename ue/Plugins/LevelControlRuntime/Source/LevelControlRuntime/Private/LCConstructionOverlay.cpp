#include "LCPresentation.h"
#include "Engine/Engine.h"
#include "Engine/GameViewportClient.h"
#include "GameFramework/PlayerController.h"
#include "Kismet/GameplayStatics.h"
#include "Misc/Paths.h"
#include "Rendering/DrawElements.h"
#include "Widgets/SLeafWidget.h"
#include "Framework/Application/SlateApplication.h"
#include "Rendering/SlateRenderer.h"
#include "Fonts/FontMeasure.h"

class SLCConstructionOverlay : public SLeafWidget
{
public:
    SLATE_BEGIN_ARGS(SLCConstructionOverlay) {} SLATE_END_ARGS()
    void Construct(const FArguments&, ULCPresentation* InPresentation)
    {
        Presentation = InPresentation;
        SetVisibility(EVisibility::HitTestInvisible);
        ForceVolatile(true);
        auto Composite = MakeShared<FCompositeFont>();
        Composite->DefaultTypeface.Fonts.Add(FTypefaceEntry(TEXT("Regular"),
            FPaths::EngineContentDir() / TEXT("Slate/Fonts/DroidSansFallback.ttf"),
            EFontHinting::Default, EFontLoadingPolicy::LazyLoad));
        Font = FSlateFontInfo(Composite, 16);
    }
    virtual FVector2D ComputeDesiredSize(float) const override { return FVector2D::ZeroVector; }
    virtual int32 OnPaint(const FPaintArgs&, const FGeometry& Geometry, const FSlateRect&,
        FSlateWindowElementList& Out, int32 Layer, const FWidgetStyle&, bool) const override
    {
        const auto* Value = Presentation.Get();
        if (!Value) return Layer;
        const FString Caption = Value->OverlayCaption();
        if (Caption.IsEmpty()) return Layer;
        const FVector2D Size = Geometry.GetLocalSize();
        auto Text = [&](const FString& Content, FVector2D Position)
        {
            const auto Measure = FSlateApplication::Get().GetRenderer()->GetFontMeasureService();
            FString Visible = Content;
            FVector2D TextSize = Measure->Measure(Visible, Font);
            while (Visible.Len() > 2 && TextSize.X > Size.X - Position.X - 24)
            {
                Visible.LeftChopInline(2); Visible += TEXT("…");
                TextSize = Measure->Measure(Visible, Font);
            }
            FSlateDrawElement::MakeText(Out, Layer, Geometry.ToPaintGeometry(TextSize,
                FSlateLayoutTransform(Position + FVector2D(1, 1))), Visible, Font, ESlateDrawEffect::None, FLinearColor::Black);
            FSlateDrawElement::MakeText(Out, Layer + 1, Geometry.ToPaintGeometry(TextSize,
                FSlateLayoutTransform(Position)), Visible, Font, ESlateDrawEffect::None, FLinearColor::White);
        };
        Text(Caption, FVector2D(24, FMath::Max(24.0, Size.Y - 76)));
        Text(TEXT("Esc 跳过演出 · 移动或转动视角返回玩家"), FVector2D(24, FMath::Max(48.0, Size.Y - 48)));
        FVector Position; FString Label; FVector2D Screen;
        auto* Player = UGameplayStatics::GetPlayerController(Value->GetWorld(), 0);
        if (Player && Value->OverlayFocus(Position, Label) && Player->ProjectWorldLocationToScreen(Position, Screen))
        {
            int32 Width, Height; Player->GetViewportSize(Width, Height);
            Screen *= Size / FVector2D(FMath::Max(Width, 1), FMath::Max(Height, 1));
            Screen.X = FMath::Clamp(Screen.X, 24.0, FMath::Max(24.0, Size.X - 240));
            Screen.Y = FMath::Clamp(Screen.Y - 30, 36.0, FMath::Max(36.0, Size.Y - 120));
            Text(Label, Screen);
        }
        return Layer + 2;
    }
private:
    TWeakObjectPtr<ULCPresentation> Presentation;
    FSlateFontInfo Font;
};

UWorld* ULCPresentation::GetWorld() const
{
    return Host ? Host->GetWorld() : nullptr;
}

void ULCPresentation::BeginDestroy()
{
    if (Overlay && GEngine && GEngine->GameViewport)
        GEngine->GameViewport->RemoveViewportWidgetContent(Overlay.ToSharedRef());
    Overlay.Reset();
    Super::BeginDestroy();
}

void ULCPresentation::UpdateOverlay()
{
    if (!Overlay && GEngine && GEngine->GameViewport && Host && Host->GetWorld() == GEngine->GameViewport->GetWorld())
    {
        Overlay = SNew(SLCConstructionOverlay, this);
        GEngine->GameViewport->AddViewportWidgetContent(Overlay.ToSharedRef(), 20);
    }
}

FString ULCPresentation::OverlayCaption() const
{
    if (!Director || (State != TEXT("previewing") && State != TEXT("settled") && State != TEXT("committed"))) return TEXT("");
    if (State == TEXT("committed")) return TEXT("结构已完成");
    return Caption;
}

bool ULCPresentation::OverlayFocus(FVector& Position, FString& Text) const
{
    for (const auto& Pair : GrowthGroups)
    {
        const auto& Group = Pair.Value;
        if (Group.Progress <= 0 || Group.Progress >= 1) continue;
        Position = Group.Bounds.GetCenter(); Position.Z = Group.Height;
        const auto* Label = GroupLabels.Find(Group.Name);
        Text = (Label ? *Label : Group.Name) + TEXT(" 生成中"); return true;
    }
    return false;
}
