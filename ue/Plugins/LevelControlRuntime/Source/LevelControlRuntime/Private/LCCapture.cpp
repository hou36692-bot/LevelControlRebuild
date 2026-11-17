#include "LCTxService.h"
#include "LCPresentation.h"
#include "Async/Async.h"
#include "Camera/CameraActor.h"
#include "Camera/CameraComponent.h"
#include "Engine/Engine.h"
#include "Engine/GameViewportClient.h"
#include "Engine/World.h"
#include "GameFramework/PlayerController.h"
#include "Kismet/GameplayStatics.h"
#include "ImageUtils.h"
#include "Misc/Paths.h"
#include "Misc/FileHelper.h"
#include "HAL/FileManager.h"
#include "Modules/ModuleManager.h"

void ULCTxService::ReturnReviewCamera()
{
    if (ScreenshotHandle.IsValid())
    {
        UGameViewportClient::OnScreenshotCaptured().Remove(ScreenshotHandle);
        ScreenshotHandle.Reset(); FScreenshotRequest::Reset();
    }
    if (ReviewCamera)
    {
        auto* Controller = UGameplayStatics::GetPlayerController(Host->GetWorld(), 0);
        if (Controller) Controller->SetViewTarget(ReviewPreviousView.IsValid() ? ReviewPreviousView.Get() : Controller->GetPawn());
        ReviewCamera->Destroy(); ReviewCamera = nullptr;
    }
    ReviewPreviousView.Reset(); CaptureDelayFrames = 0;
}

void ULCTxService::ScreenshotPixels(int32 Width, int32 Height, const TArray<FColor>& Pixels)
{
    UGameViewportClient::OnScreenshotCaptured().Remove(ScreenshotHandle); ScreenshotHandle.Reset();
    TArray<FColor> Copy = Pixels;
    for (auto& Pixel : Copy) Pixel.A = 255;
    const FString Path = CapturePath;
    CaptureJob = Async(EAsyncExecution::ThreadPool, [Copy = MoveTemp(Copy), Width, Height, Path] {
        TArray64<uint8> Bytes;
        FImageUtils::PNGCompressImageArray(Width, Height, MakeArrayView(Copy), Bytes);
        return !Bytes.IsEmpty() && FFileHelper::SaveArrayToFile(Bytes, *Path);
    });
}

void ULCTxService::BeginScreenshot()
{
    if (CaptureIndex >= CaptureViews.Num()) return;
    const auto View = CaptureViews[CaptureIndex]->AsObject();
    CapturePath = View->GetStringField(TEXT("path"));
    ScreenshotHandle = UGameViewportClient::OnScreenshotCaptured().AddUObject(this, &ULCTxService::ScreenshotPixels);
    FScreenshotRequest::RequestScreenshot(CapturePath, CaptureIncludeUI, false);
}

TSharedPtr<FJsonObject> ULCTxService::Capture(const TSharedPtr<FJsonObject>& Request)
{
    FString Id, Name, Camera = TEXT("player");
    if (!Request || !Request->TryGetStringField(TEXT("req_id"), Id) || Id.IsEmpty() || Id.Len() > 128)
        return MakeReceipt(TEXT(""), TEXT("rejected"), TEXT("req_id is required"));
    if (Receipts.Contains(Id)) return Receipts[Id];
    if (IsBusy() || FScreenshotRequest::IsScreenshotRequested() || (CaptureJob.IsValid() && !CaptureJob.IsReady()))
        return MakeReceipt(Id, TEXT("rejected"), TEXT("Capture service is busy"));
    bool IncludeUI = false;
    if (Request->HasField(TEXT("include_ui")) && !Request->TryGetBoolField(TEXT("include_ui"), IncludeUI))
        return MakeReceipt(Id, TEXT("rejected"), TEXT("include_ui must be boolean"));
    Request->TryGetStringField(TEXT("camera"), Camera);
    if (!Request->TryGetStringField(TEXT("path"), Name) || Name.IsEmpty() || Name.Len() > 128 ||
        FPaths::GetCleanFilename(Name) != Name || FPaths::GetExtension(Name).ToLower() != TEXT("png") || Camera != TEXT("player"))
        return MakeReceipt(Id, TEXT("rejected"), TEXT("Use a PNG filename and the player camera"));
    for (TCHAR Char : Name) if (!(FChar::IsAlnum(Char) || Char == '-' || Char == '_' || Char == '.'))
        return MakeReceipt(Id, TEXT("rejected"), TEXT("Invalid capture filename"));
    FVector Center = FVector::ZeroVector;
    double Radius = 1800, Height = 800;
    const bool Multiple = Request->HasField(TEXT("center_cm"));
    const FString PresentationState = Presentation->Status()->GetStringField(TEXT("state"));
    if (Multiple && (QueuedCommit || EnvironmentRate > 0 || PresentationState == TEXT("previewing") ||
        (!DesignPreview && PresentationState == TEXT("settled")) || PresentationState == TEXT("committed")))
        return MakeReceipt(Id, TEXT("rejected"), TEXT("Finish or cancel moving previews before four-view capture"));
    if (Multiple && (!LCReadVector(Request, TEXT("center_cm"), Center) ||
        !Request->TryGetNumberField(TEXT("radius_cm"), Radius) || !Request->TryGetNumberField(TEXT("height_cm"), Height) ||
        !FMath::IsFinite(Radius) || !FMath::IsFinite(Height) || Radius < 100 || Radius > 100000 || Height < 0 || Height > 100000))
        return MakeReceipt(Id, TEXT("rejected"), TEXT("Four-view capture requires finite center, radius 100..100000 cm and height 0..100000 cm"));
    auto* Controller = UGameplayStatics::GetPlayerController(Host->GetWorld(), 0);
    if (Multiple && !Controller) return MakeReceipt(Id, TEXT("rejected"), TEXT("No player camera controller"));
    FModuleManager::Get().LoadModule(TEXT("ImageWrapper"));
    if (CaptureJob.IsValid()) { CaptureJob.Get(); CaptureJob = TFuture<bool>(); }
    Pending = FLCStoreEdit(); PendingMode.Reset();
    CaptureIncludeUI = IncludeUI;
    Pending.RequestId = Id; Pending.TransactionId = TEXT("capture-") + FGuid::NewGuid().ToString(EGuidFormats::Digits);
    Pending.Kind = TEXT("capture"); Pending.Command = Name; Pending.Version = Version;
    const FString Directory = RuntimeDataDirectory / TEXT("Captures") / Pending.TransactionId;
    if (!IFileManager::Get().MakeDirectory(*Directory, true)) return MakeReceipt(Id, TEXT("rejected"), TEXT("Cannot create screenshot directory"));
    CaptureViews.Reset(); CapturedViews.Reset(); CaptureIndex = 0;
    const TCHAR* Names[] = {TEXT("north"), TEXT("east"), TEXT("south"), TEXT("west")};
    const FVector Offsets[] = {FVector(0,-1,0), FVector(1,0,0), FVector(0,1,0), FVector(-1,0,0)};
    for (int32 Index = 0; Index < (Multiple ? 4 : 1); ++Index)
    {
        auto View = MakeShared<FJsonObject>();
        View->SetStringField(TEXT("id"), Multiple ? Names[Index] : TEXT("player"));
        View->SetStringField(TEXT("path"), Directory / (Multiple ? FString::Printf(TEXT("%s-v%lld-%s.png"), *FPaths::GetBaseFilename(Name), Version, Names[Index]) : Name));
        if (Multiple)
        {
            View->SetArrayField(TEXT("position_cm"), LCVector(Center + Offsets[Index] * Radius + FVector(0,0,Height)));
            View->SetArrayField(TEXT("target_cm"), LCVector(Center)); View->SetNumberField(TEXT("fov_deg"), 65);
        }
        CaptureViews.Add(MakeShared<FJsonValueObject>(View));
    }
    Pending.Receipt = MakeReceipt(Id, TEXT("scheduled")); Pending.Receipt->SetStringField(TEXT("tx_id"), Pending.TransactionId);
    Pending.Receipt->SetStringField(TEXT("path"), Multiple ? Directory : CaptureViews[0]->AsObject()->GetStringField(TEXT("path")));
    Pending.Receipt->SetBoolField(TEXT("preview"), EnvironmentPreview.IsValid() || DesignPreview.IsValid());
    Receipts.Add(Id, Pending.Receipt);
    Started = FPlatformTime::Seconds(); MaxFrameMs = 0; FirstVisible = 0; Phase = EPhase::Capture;
    if (Multiple)
    {
        ReviewPreviousView = Controller->GetViewTarget();
        ReviewCamera = Host->GetWorld()->SpawnActor<ACameraActor>();
        if (!ReviewCamera) { Finish(TEXT("failed"), TEXT("Cannot allocate review camera")); return Receipts[Id]; }
        ReviewCamera->SetOwner(Host); ReviewCamera->SetFlags(RF_Transient);
        ReviewCamera->GetCameraComponent()->SetFieldOfView(65);
        FVector Position; LCReadVector(CaptureViews[0]->AsObject(), TEXT("position_cm"), Position);
        ReviewCamera->SetActorLocation(Position); ReviewCamera->SetActorRotation((Center - Position).Rotation());
        Controller->SetViewTarget(ReviewCamera); CaptureDelayFrames = 3;
    }
    else BeginScreenshot();
    return Pending.Receipt;
}
