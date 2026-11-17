#include "LCTxService.h"
#include "LCPresentation.h"
#include "Async/Async.h"
#include "Engine/Engine.h"
#include "Engine/GameViewportClient.h"
#include "EngineUtils.h"
#include "Camera/PlayerCameraManager.h"
#include "Camera/CameraActor.h"
#include "GameFramework/PlayerController.h"
#include "Kismet/GameplayStatics.h"
#include "Components/LightComponentBase.h"
#include "Components/LocalLightComponent.h"
#include "HAL/PlatformMemory.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "Misc/EngineVersion.h"
#include "HAL/FileManager.h"
#include "RHIStats.h"
#include "RHIGlobals.h"
#include "SceneView.h"
#include "ImageUtils.h"
#include "ImageCore.h"
#include "Modules/ModuleManager.h"

namespace
{
double LCPercentile(TArray<double> Values, double Fraction)
{
    Values.Sort();
    return Values[FMath::Clamp(FMath::CeilToInt(Values.Num() * Fraction) - 1, 0, Values.Num() - 1)];
}
}

TSharedPtr<FJsonObject> ULCTxService::FrameHistoryRead(int64 Since) const
{
    auto Result = MakeShared<FJsonObject>();
    TArray<FLCFrameSample> Ordered = FrameHistory;
    Ordered.Sort([](const FLCFrameSample& A, const FLCFrameSample& B) { return A.Sequence < B.Sequence; });
    TArray<TSharedPtr<FJsonValue>> Values;
    for (const auto& Sample : Ordered)
    {
        if (int64(Sample.Sequence) <= Since) continue;
        auto Value = MakeShared<FJsonObject>();
        Value->SetNumberField(TEXT("frame"), double(Sample.Sequence)); Value->SetNumberField(TEXT("ms"), Sample.Ms);
        Value->SetNumberField(TEXT("version"), Sample.Version); Value->SetBoolField(TEXT("editing"), Sample.Editing);
        Value->SetBoolField(TEXT("capture"), Sample.Capture); Values.Add(MakeShared<FJsonValueObject>(Value));
    }
    Result->SetArrayField(TEXT("frames"), Values); Result->SetNumberField(TEXT("last"), double(FrameSequence));
    Result->SetBoolField(TEXT("truncated"), Since > 0 && !Ordered.IsEmpty() && uint64(Since + 1) < Ordered[0].Sequence);
    return Result;
}

TSharedPtr<FJsonObject> ULCTxService::ReviewControl(const TSharedPtr<FJsonObject>& Request)
{
    FString Id, Action, Error;
    if (!Request || !Request->TryGetStringField(TEXT("req_id"), Id) || Id.IsEmpty() || Id.Len() > 128)
        return MakeReceipt(TEXT(""), TEXT("rejected"), TEXT("req_id must contain 1..128 characters"));
    if (Receipts.Contains(Id)) return Receipts[Id];
    Request->TryGetStringField(TEXT("action"), Action);
    if (Action == TEXT("compare")) return CompareCaptures(Request);
    if (Action == TEXT("cancel"))
    {
        if (Phase == EPhase::Capture || Phase == EPhase::Measure)
        {
            ReturnReviewCamera();
            Finish(TEXT("failed"), TEXT("Review cancelled by user"));
        }
        auto Result = MakeReceipt(Id, TEXT("applied"));
        Result->SetStringField(TEXT("tx_id"), TEXT("review-control-") + FGuid::NewGuid().ToString(EGuidFormats::Digits));
        Receipts.Add(Id, Result); return Result;
    }
    double Frames = 120;
    if (Action != TEXT("performance") || !Request->TryGetNumberField(TEXT("frames"), Frames) || !FMath::IsFinite(Frames) ||
        Frames != FMath::FloorToDouble(Frames) || Frames < 30 || Frames > 3600)
        Error = TEXT("Performance sampling requires frames within 30..3600");
    if (IsBusy() || EnvironmentPreview || DesignPreview || QueuedCommit) Error = TEXT("Finish transactions and previews before measurement");
    const FString PresentationState = Presentation->Status()->GetStringField(TEXT("state"));
    if (PresentationState == TEXT("previewing") || PresentationState == TEXT("settled") || PresentationState == TEXT("committed"))
        Error = TEXT("Finish or cancel construction before performance measurement");
    if (CaptureJob.IsValid() && !CaptureJob.IsReady()) Error = TEXT("Wait for image encoding before measuring performance");
    if (!Error.IsEmpty()) { auto Result = MakeReceipt(Id, TEXT("rejected"), Error); Receipts.Add(Id, Result); return Result; }
    Pending = FLCStoreEdit(); PendingMode.Reset();
    Pending.RequestId = Id; Pending.TransactionId = TEXT("review-") + FGuid::NewGuid().ToString(EGuidFormats::Digits);
    Pending.Kind = TEXT("review"); Pending.Command = TEXT("performance"); Pending.Version = Version;
    Pending.Receipt = MakeReceipt(Id, TEXT("scheduled")); Pending.Receipt->SetStringField(TEXT("tx_id"), Pending.TransactionId);
    Receipts.Add(Id, Pending.Receipt);
    MeasurementFrames.Reset(); MeasurementDrawCalls.Reset(); MeasurementTarget = int32(Frames);
    Started = FPlatformTime::Seconds(); Phase = EPhase::Measure;
    return Pending.Receipt;
}

TSharedPtr<FJsonObject> ULCTxService::CompareCaptures(const TSharedPtr<FJsonObject>& Request)
{
    const FString Id = Request->GetStringField(TEXT("req_id"));
    FString A, B, Name, Error;
    if (IsBusy()) Error = TEXT("Transaction service is busy");
    if (!Request->TryGetStringField(TEXT("a"), A) || !Request->TryGetStringField(TEXT("b"), B) ||
        !Request->TryGetStringField(TEXT("name"), Name) || !LCValidId(Name)) Error = TEXT("Comparison requires capture request IDs a/b and a kebab-case name");
    auto Left = Receipt(A), Right = Receipt(B);
    const TArray<TSharedPtr<FJsonValue>>* LeftViews = nullptr;
    const TArray<TSharedPtr<FJsonValue>>* RightViews = nullptr;
    if (Left->GetStringField(TEXT("status")) != TEXT("applied") || Right->GetStringField(TEXT("status")) != TEXT("applied") ||
        !Left->TryGetArrayField(TEXT("captures"), LeftViews) || !Right->TryGetArrayField(TEXT("captures"), RightViews) ||
        LeftViews->Num() != 4 || RightViews->Num() != 4) Error = TEXT("Both inputs must be completed four-view captures");
    if (Error.IsEmpty()) for (int32 Index = 0; Index < 4; ++Index)
    {
        const auto L = (*LeftViews)[Index]->AsObject(), R = (*RightViews)[Index]->AsObject();
        for (const TCHAR* Key : {TEXT("id"), TEXT("position_cm"), TEXT("target_cm"), TEXT("fov_deg")})
        {
            auto LValue = L->TryGetField(Key), RValue = R->TryGetField(Key);
            if (!LValue || !RValue || !FJsonValue::CompareEqual(*LValue, *RValue)) Error = TEXT("A/B camera definitions differ");
        }
    }
    if (!Error.IsEmpty()) { auto Result = MakeReceipt(Id, TEXT("rejected"), Error); Receipts.Add(Id, Result); return Result; }
    Pending = FLCStoreEdit(); PendingMode.Reset();
    Pending.RequestId = Id; Pending.TransactionId = TEXT("compare-") + FGuid::NewGuid().ToString(EGuidFormats::Digits);
    Pending.Kind = TEXT("review"); Pending.Command = TEXT("compare ") + A + TEXT(" ") + B; Pending.Version = Version;
    auto Scheduled = MakeReceipt(Id, TEXT("scheduled")); Scheduled->SetStringField(TEXT("tx_id"), Pending.TransactionId);
    Receipts.Add(Id, Scheduled);
    Pending.Receipt = LCParseJson(LCJsonString(Scheduled)); Pending.Receipt->SetStringField(TEXT("status"), TEXT("applied"));
    const FString Directory = FPaths::ConvertRelativePathToFull(FPaths::ProjectSavedDir() / TEXT("LevelControl/Reviews") / Pending.TransactionId);
    IFileManager::Get().MakeDirectory(*Directory, true);
    TArray<TSharedPtr<FJsonValue>> Comparisons;
    for (int32 Index = 0; Index < 4; ++Index)
    {
        auto Item = MakeShared<FJsonObject>();
        const FString ViewId = (*LeftViews)[Index]->AsObject()->GetStringField(TEXT("id"));
        Item->SetStringField(TEXT("id"), ViewId);
        Item->SetStringField(TEXT("a"), (*LeftViews)[Index]->AsObject()->GetStringField(TEXT("path")));
        Item->SetStringField(TEXT("b"), (*RightViews)[Index]->AsObject()->GetStringField(TEXT("path")));
        Item->SetStringField(TEXT("path"), Directory / (Name + TEXT("-") + ViewId + TEXT(".png")));
        Comparisons.Add(MakeShared<FJsonValueObject>(Item));
    }
    Pending.Receipt->SetArrayField(TEXT("comparisons"), Comparisons);
    Pending.Receipt->SetNumberField(TEXT("a_version"), Left->GetNumberField(TEXT("version")));
    Pending.Receipt->SetNumberField(TEXT("b_version"), Right->GetNumberField(TEXT("version")));
    Pending.Receipt->SetStringField(TEXT("path"), Directory / TEXT("comparison.md"));
    Started = FPlatformTime::Seconds(); Phase = EPhase::Persist;
    FModuleManager::Get().LoadModule(TEXT("ImageWrapper"));
    StoreJob = Async(EAsyncExecution::ThreadPool, [this, Comparisons, Directory, A, B] {
        FString Markdown = TEXT("# A/B comparison\n\nLeft: ") + A + TEXT("\n\nRight: ") + B + TEXT("\n");
        for (const auto& Value : Comparisons)
        {
            const auto Item = Value->AsObject(); FImage LeftImage, RightImage;
            if (!FImageUtils::LoadImage(*Item->GetStringField(TEXT("a")), LeftImage) ||
                !FImageUtils::LoadImage(*Item->GetStringField(TEXT("b")), RightImage) ||
                LeftImage.SizeX != RightImage.SizeX || LeftImage.SizeY != RightImage.SizeY)
            { Store.Error = TEXT("Cannot decode matched A/B screenshots"); return false; }
            LeftImage.ChangeFormat(ERawImageFormat::BGRA8, EGammaSpace::sRGB);
            RightImage.ChangeFormat(ERawImageFormat::BGRA8, EGammaSpace::sRGB);
            FImage Joined; Joined.Init(LeftImage.SizeX * 2 + 12, LeftImage.SizeY, ERawImageFormat::BGRA8, EGammaSpace::sRGB);
            for (int32 Row = 0; Row < Joined.SizeY; ++Row)
            {
                auto* Output = reinterpret_cast<FColor*>(Joined.RawData.GetData()) + Row * Joined.SizeX;
                FMemory::Memcpy(Output, LeftImage.RawData.GetData() + int64(Row) * LeftImage.SizeX * 4, LeftImage.SizeX * 4);
                for (int32 Gap = 0; Gap < 12; ++Gap) Output[LeftImage.SizeX + Gap] = FColor(32,32,32,255);
                FMemory::Memcpy(Output + LeftImage.SizeX + 12, RightImage.RawData.GetData() + int64(Row) * RightImage.SizeX * 4, RightImage.SizeX * 4);
            }
            const FString Path = Item->GetStringField(TEXT("path"));
            if (!FImageUtils::SaveImageByExtension(*Path, Joined)) { Store.Error = TEXT("Cannot save A/B PNG"); return false; }
            Markdown += TEXT("\n## ") + Item->GetStringField(TEXT("id")) + TEXT("\n\n![A/B](") + FPaths::GetCleanFilename(Path) + TEXT(")\n");
        }
        return FFileHelper::SaveStringToFile(Markdown, *(Directory / TEXT("comparison.md"))) &&
            FFileHelper::SaveStringToFile(LCJsonString(Pending.Receipt), *(Directory / TEXT("comparison.json"))) && Store.Commit(Pending);
    });
    return Scheduled;
}

void ULCTxService::TickMeasurement(float DeltaTime)
{
    if (DeltaTime <= 0 || !FMath::IsFinite(DeltaTime)) { Finish(TEXT("failed"), TEXT("Invalid frame interval")); return; }
    MeasurementFrames.Add(DeltaTime * 1000.0);
    MeasurementDrawCalls.Add(GNumDrawCallsRHI[0]);
    if (MeasurementFrames.Num() < MeasurementTarget) return;
    auto Report = MakeShared<FJsonObject>();
    TArray<double> Fps, Draws;
    TArray<TSharedPtr<FJsonValue>> RawFrames, RawDraws;
    for (int32 Index = 0; Index < MeasurementFrames.Num(); ++Index)
    {
        Fps.Add(1000.0 / MeasurementFrames[Index]); Draws.Add(MeasurementDrawCalls[Index]);
        RawFrames.Add(MakeShared<FJsonValueNumber>(MeasurementFrames[Index]));
        RawDraws.Add(MakeShared<FJsonValueNumber>(MeasurementDrawCalls[Index]));
    }
    Report->SetNumberField(TEXT("world_version"), Version);
    Report->SetStringField(TEXT("created_utc"), FDateTime::UtcNow().ToIso8601());
    Report->SetStringField(TEXT("engine"), FEngineVersion::Current().ToString());
    Report->SetStringField(TEXT("cpu"), FPlatformMisc::GetCPUBrand());
    Report->SetStringField(TEXT("gpu"), GRHIAdapterName);
    Report->SetStringField(TEXT("driver"), GRHIAdapterUserDriverVersion);
    const auto Memory = FPlatformMemory::GetStats();
    Report->SetNumberField(TEXT("physical_memory_bytes"), double(Memory.TotalPhysical));
    Report->SetNumberField(TEXT("process_memory_bytes"), double(Memory.UsedPhysical));
    if (GEngine->GameViewport && GEngine->GameViewport->Viewport)
    {
        const auto Size = GEngine->GameViewport->Viewport->GetSizeXY();
        Report->SetNumberField(TEXT("width"), Size.X); Report->SetNumberField(TEXT("height"), Size.Y);
    }
    Report->SetNumberField(TEXT("samples"), MeasurementFrames.Num());
    Report->SetNumberField(TEXT("entity_count"), Entities.Num());
    Report->SetNumberField(TEXT("fps_p50"), LCPercentile(Fps, 0.5));
    Report->SetNumberField(TEXT("fps_p95"), LCPercentile(Fps, 0.95));
    Report->SetNumberField(TEXT("frame_ms_p95"), LCPercentile(MeasurementFrames, 0.95));
    Report->SetNumberField(TEXT("frame_ms_max"), LCPercentile(MeasurementFrames, 1));
    Report->SetNumberField(TEXT("draw_calls_p50"), LCPercentile(Draws, 0.5));
    Report->SetNumberField(TEXT("draw_calls_p95"), LCPercentile(Draws, 0.95));
    Report->SetBoolField(TEXT("draw_calls_available"), LCPercentile(Draws, 1) > 0);
    Report->SetArrayField(TEXT("frame_ms"), RawFrames); Report->SetArrayField(TEXT("draw_calls"), RawDraws);
    Report->SetStringField(TEXT("fps_source"), TEXT("Game frame intervals; no samples removed"));
    Report->SetStringField(TEXT("draw_calls_source"), TEXT("GNumDrawCallsRHI GPU 0, latest completed RHI frame"));
    int32 Lights = 0;
    auto* Controller = UGameplayStatics::GetPlayerController(Host->GetWorld(), 0);
    FConvexVolume Frustum;
    if (Controller && Controller->PlayerCameraManager)
    {
        const auto View = Controller->PlayerCameraManager->GetCameraCacheView();
        const FMatrix Rotation = FInverseRotationMatrix(View.Rotation) * FMatrix(
            FPlane(0,0,1,0), FPlane(1,0,0,0), FPlane(0,1,0,0), FPlane(0,0,0,1));
        GetViewFrustumBounds(Frustum, FTranslationMatrix(-View.Location) * Rotation * View.CalculateProjectionMatrix(), false);
    }
    for (TActorIterator<AActor> It(Host->GetWorld()); It; ++It)
    {
        TArray<ULightComponentBase*> Components; It->GetComponents(Components);
        for (auto* Light : Components)
        {
            if (!Light->IsVisible() || It->IsHidden() || Light->Intensity <= 0) continue;
            const auto* Local = Cast<ULocalLightComponent>(Light);
            if (!Local || Frustum.IntersectSphere(Local->GetComponentLocation(), Local->AttenuationRadius)) ++Lights;
        }
    }
    Report->SetNumberField(TEXT("visible_lights"), Lights);
    Report->SetStringField(TEXT("lights_source"), TEXT("Enabled scene lights intersecting camera frustum; directional and sky lights included"));
    const FString Directory = FPaths::ConvertRelativePathToFull(FPaths::ProjectSavedDir() / TEXT("LevelControl/Reviews") / Pending.TransactionId);
    IFileManager::Get().MakeDirectory(*Directory, true);
    Pending.Receipt = LCParseJson(LCJsonString(Pending.Receipt));
    Pending.Receipt->SetStringField(TEXT("status"), TEXT("applied")); Pending.Receipt->SetObjectField(TEXT("report"), Report);
    Pending.Receipt->SetStringField(TEXT("path"), Directory / TEXT("performance.json"));
    const FString Json = LCJsonString(Report);
    const FString Markdown = FString::Printf(TEXT("# Performance report\n\nWorld version: %lld\n\nSamples: %d\n\nFPS p50: %.2f; FPS p95: %.2f; frame p95: %.2f ms; max: %.2f ms.\n\nDraw calls p50: %.0f; visible lights: %d; process memory: %.2f MiB.\n\nRaw data: performance.json. No samples removed.\n"), Version, MeasurementFrames.Num(), LCPercentile(Fps,0.5), LCPercentile(Fps,0.95), LCPercentile(MeasurementFrames,0.95), LCPercentile(MeasurementFrames,1), LCPercentile(Draws,0.5), Lights, double(Memory.UsedPhysical)/1048576);
    Phase = EPhase::Persist;
    StoreJob = Async(EAsyncExecution::ThreadPool, [this, Directory, Json, Markdown] {
        return FFileHelper::SaveStringToFile(Json, *(Directory / TEXT("performance.json"))) &&
            FFileHelper::SaveStringToFile(Markdown, *(Directory / TEXT("performance.md"))) && Store.Commit(Pending);
    });
}
