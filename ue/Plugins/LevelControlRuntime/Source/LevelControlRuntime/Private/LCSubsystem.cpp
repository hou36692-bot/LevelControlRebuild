#include "LCSubsystem.h"
#include "LCLog.h"
#include "LCSurface.h"
#include "LCSettings.h"
#include "LCTxService.h"
#include "Dom/JsonObject.h"
#include "Engine/World.h"
#include "HttpPath.h"
#include "HttpServerModule.h"
#include "HttpServerResponse.h"
#include "IHttpRouter.h"
#include "Misc/CommandLine.h"
#include "Misc/ConfigCacheIni.h"
#include "Misc/Parse.h"
#include "Serialization/JsonSerializer.h"
#include "LCSelector.h"
#include "Components/CapsuleComponent.h"
#include "GameFramework/Character.h"
#include "GameFramework/CharacterMovementComponent.h"
#include "Kismet/GameplayStatics.h"

bool ULCSubsystem::ShouldCreateSubsystem(UObject* Outer) const
{
    const UWorld* World = Cast<UWorld>(Outer);
    return World && World->WorldType == EWorldType::Game && !IsRunningCommandlet();
}

void ULCSubsystem::OnWorldBeginPlay(UWorld& World)
{
    Super::OnWorldBeginPlay(World);
    Transactions = NewObject<ULCTxService>(this);
    if (!Transactions->Initialize(&World))
    {
        UE_LOG(LogLC, Error, TEXT("Template initialization failed"));
        return;
    }
    Port = GetDefault<ULCSettings>()->Port;
    FParse::Value(FCommandLine::Get(), TEXT("LCPort="), Port);
    if (Port < 1024 || Port > 65535)
    {
        UE_LOG(LogLC, Error, TEXT("Invalid LCPort: %d"), Port);
        return;
    }
    // Enforce loopback even if a project's default listener address changes.
    GConfig->SetString(TEXT("HTTPServer.Listeners"), TEXT("DefaultBindAddress"), TEXT("127.0.0.1"), GEngineIni);
    auto& Server = FHttpServerModule::Get();
    Router = Server.GetHttpRouter(Port, true);
    if (!Router)
    {
        UE_LOG(LogLC, Error, TEXT("Cannot bind HTTP listener on 127.0.0.1:%d"), Port);
        return;
    }
    Routes.Add(Router->BindRoute(FHttpPath(TEXT("/health")), EHttpServerRequestVerbs::VERB_GET,
        FHttpRequestHandler::CreateWeakLambda(this, [this](const FHttpServerRequest& Request, const FHttpResultCallback& Complete)
        {
            auto Body = MakeShared<FJsonObject>();
            Body->SetBoolField(TEXT("ok"), true);
            Body->SetNumberField(TEXT("protocol"), 4);
            Body->SetBoolField(TEXT("semantic_core"), true);
            Body->SetBoolField(TEXT("fast_build"), true);
            Body->SetBoolField(TEXT("short_presentation"), true);
            Body->SetBoolField(TEXT("upward_growth"), true);
            Body->SetBoolField(TEXT("incremental_preview"), true);
            Body->SetStringField(TEXT("world"), GetWorld()->GetMapName());
            Body->SetNumberField(TEXT("port"), Port);
            FString Json;
            FJsonSerializer::Serialize(Body, TJsonWriterFactory<>::Create(&Json));
            Complete(FHttpServerResponse::Create(Json, TEXT("application/json")));
            return true;
        })));
    Server.StartAllListeners();
    const TArray<FString> ReadPaths = {TEXT("/v1/receipt"), TEXT("/v1/templates"), TEXT("/v1/entities"), TEXT("/v1/player"), TEXT("/v1/consistency"), TEXT("/v1/presentation"), TEXT("/v1/events"), TEXT("/v1/checkpoints"), TEXT("/v1/clock"), TEXT("/v1/environment"), TEXT("/v1/environment/presets"), TEXT("/v1/surfaces"), TEXT("/v1/catalog"), TEXT("/v1/frame-history")};
    const TArray<FString> WritePaths = {TEXT("/v1/edits"), TEXT("/v1/undo"), TEXT("/v1/select"), TEXT("/v1/capture"), TEXT("/v1/preview"), TEXT("/v1/commit"), TEXT("/v1/presentation"), TEXT("/v1/checkpoints"), TEXT("/v1/checks"), TEXT("/v1/player/control"), TEXT("/v1/actions"), TEXT("/v1/actions/control"), TEXT("/v1/environment"), TEXT("/v1/design"), TEXT("/v1/review")};
    auto Bind = [&](const FString& Path, EHttpServerRequestVerbs Verb)
    {
        Routes.Add(Router->BindRoute(FHttpPath(Path), Verb, FHttpRequestHandler::CreateWeakLambda(this,
            [this, Path](const FHttpServerRequest& Request, const FHttpResultCallback& Complete)
            {
                Complete(FHttpServerResponse::Create(LCJsonString(Handle(Path, Request)), TEXT("application/json")));
                return true;
            })));
    };
    Bind(TEXT("/v1/semantic-state"), EHttpServerRequestVerbs::VERB_GET);
    for (const auto& Path : {TEXT("/v1/query"), TEXT("/v1/changes"), TEXT("/v1/annotations")}) Bind(Path, EHttpServerRequestVerbs::VERB_POST);
    for (const auto& Path : ReadPaths) Bind(Path, EHttpServerRequestVerbs::VERB_GET);
    for (const auto& Path : WritePaths) Bind(Path, EHttpServerRequestVerbs::VERB_POST);
    UE_LOG(LogLC, Display, TEXT("HTTP ready: http://127.0.0.1:%d/health world=%s"), Port, *World.GetMapName());
}

void ULCSubsystem::Deinitialize()
{
    if (Router)
    {
        for (const auto& Route : Routes) if (Route) Router->UnbindRoute(Route);
        Routes.Reset();
        Router.Reset();
    }
    Super::Deinitialize();
    if (Transactions) Transactions->Shutdown();
}

void ULCSubsystem::Tick(float DeltaTime)
{
    if (Transactions) Transactions->Tick(DeltaTime);
}

static TSharedPtr<FJsonObject> LCRequestError(const FString& Error, int64 Version)
{
    auto Json = MakeShared<FJsonObject>();
    Json->SetStringField(TEXT("req_id"), TEXT(""));
    Json->SetStringField(TEXT("tx_id"), TEXT(""));
    Json->SetStringField(TEXT("status"), TEXT("rejected"));
    Json->SetNumberField(TEXT("applied"), 0);
    Json->SetArrayField(TEXT("entity_ids"), {});
    Json->SetNumberField(TEXT("version"), Version);
    Json->SetArrayField(TEXT("warnings"), {MakeShared<FJsonValueString>(Error)});
    Json->SetNumberField(TEXT("ms"), 0);
    return Json;
}

TSharedPtr<FJsonObject> ULCSubsystem::Handle(const FString& Path, const FHttpServerRequest& Request)
{
    if (Request.Verb == EHttpServerRequestVerbs::VERB_POST)
    {
        const auto* ContentType = Request.Headers.Find(TEXT("content-type"));
        if (Request.Headers.Contains(TEXT("origin")) || !ContentType || ContentType->IsEmpty() || !(*ContentType)[0].StartsWith(TEXT("application/json")))
            return LCRequestError(TEXT("Native application/json requests only"), Transactions->GetVersion());
        if (Request.Body.IsEmpty() || Request.Body.Num() > 8 * 1024 * 1024)
            return LCRequestError(TEXT("JSON body must contain 1..8388608 bytes"), Transactions->GetVersion());
        FUTF8ToTCHAR Text(reinterpret_cast<const ANSICHAR*>(Request.Body.GetData()), Request.Body.Num());
        auto Json = LCParseJson(FString(Text.Length(), Text.Get()));
        if (!Json) return LCRequestError(TEXT("Invalid JSON object"), Transactions->GetVersion());
        if (Path == TEXT("/v1/query")) return Transactions->SemanticQuery(Json);
        if (Path == TEXT("/v1/changes")) return Transactions->SemanticChanges(Json);
        if (Path == TEXT("/v1/annotations")) return Transactions->Submit(Json, false, TEXT("annotations"));
        if (Path == TEXT("/v1/review")) return Transactions->ReviewControl(Json);
        if (Path == TEXT("/v1/design")) return Transactions->DesignControl(Json);
        if (Path == TEXT("/v1/environment")) return Transactions->EnvironmentControl(Json);
        if (Path == TEXT("/v1/select")) return Transactions->Select(Json);
        if (Path == TEXT("/v1/capture")) return Transactions->Capture(Json);
        if (Path == TEXT("/v1/preview")) return Transactions->Preview(Json);
        if (Path == TEXT("/v1/commit")) return Transactions->Commit(Json);
        if (Path == TEXT("/v1/presentation")) return Transactions->Control(Json);
        if (Path == TEXT("/v1/checkpoints")) return Transactions->Submit(Json, false, TEXT("checkpoint"));
        if (Path == TEXT("/v1/checks")) return Transactions->Check(Json);
        if (Path == TEXT("/v1/player/control")) return Transactions->PlayerControl(Json);
        if (Path == TEXT("/v1/actions")) return Transactions->Submit(Json, false, TEXT("action"));
        if (Path == TEXT("/v1/actions/control")) return Transactions->ActionControl(Json);
        return Transactions->Submit(Json, Path == TEXT("/v1/undo"));
    }
    if (Path == TEXT("/v1/semantic-state")) return Transactions->SemanticRead(Request.QueryParams.FindRef(TEXT("source")));
    if (Path == TEXT("/v1/frame-history"))
    {
        int64 Since = 0;
        if (Request.QueryParams.Contains(TEXT("since")) && (!LexTryParseString(Since, *Request.QueryParams.FindRef(TEXT("since"))) || Since < 0))
            return LCRequestError(TEXT("since must be nonnegative"), Transactions->GetVersion());
        return Transactions->FrameHistoryRead(Since);
    }
    if (Path == TEXT("/v1/catalog")) return LCAssetCatalog();
    if (Path == TEXT("/v1/surfaces")) { auto Result = MakeShared<FJsonObject>(); Result->SetObjectField(TEXT("presets"), LCSurfacePresets()); return Result; }
    if (Path == TEXT("/v1/environment") || Path == TEXT("/v1/environment/presets")) return Transactions->EnvironmentRead(Path.EndsWith(TEXT("/presets")));
    if (Path == TEXT("/v1/receipt")) return Transactions->Receipt(Request.QueryParams.FindRef(TEXT("req_id")));
    if (Path == TEXT("/v1/clock"))
    { auto Clock = MakeShared<FJsonObject>(); Clock->SetNumberField(TEXT("clock_s"), FPlatformTime::Seconds()); return Clock; }
    if (Path == TEXT("/v1/consistency")) return Transactions->Consistency();
    if (Path == TEXT("/v1/presentation")) return Transactions->PresentationStatus();
    if (Path == TEXT("/v1/checkpoints")) return Transactions->Checkpoints();
    if (Path == TEXT("/v1/events"))
    {
        int64 Since = 0;
        if (Request.QueryParams.Contains(TEXT("since")) && (!LexTryParseString(Since, *Request.QueryParams.FindRef(TEXT("since"))) || Since < 0))
            return LCRequestError(TEXT("since must be nonnegative"), Transactions->GetVersion());
        return Transactions->Events(Since);
    }
    auto Json = MakeShared<FJsonObject>();
    if (Path == TEXT("/v1/templates"))
    {
        TArray<TSharedPtr<FJsonValue>> Values;
        for (const auto& Template : LCTemplates())
        {
            auto Value = MakeShared<FJsonObject>();
            Value->SetStringField(TEXT("id"), Template.Id);
            Value->SetArrayField(TEXT("size_cm"), LCVector(Template.Size));
            Values.Add(MakeShared<FJsonValueObject>(Value));
        }
        Json->SetArrayField(TEXT("templates"), Values);
        return Json;
    }
    const auto* Player = UGameplayStatics::GetPlayerCharacter(GetWorld(), 0);
    if (Path == TEXT("/v1/player"))
    {
        if (!Player) return LCRequestError(TEXT("No player character"), Transactions->GetVersion());
        const double Half = Player->GetCapsuleComponent()->GetScaledCapsuleHalfHeight();
        Json->SetArrayField(TEXT("position_cm"), LCVector(Player->GetActorLocation()));
        Json->SetArrayField(TEXT("feet_cm"), LCVector(Player->GetActorLocation() - FVector(0, 0, Half)));
        Json->SetArrayField(TEXT("forward"), LCVector(FRotator(0, Player->GetControlRotation().Yaw, 0).Vector()));
        Json->SetNumberField(TEXT("yaw_deg"), Player->GetControlRotation().Yaw);
        Json->SetNumberField(TEXT("speed_cm_s"), Player->GetVelocity().Size());
        Json->SetNumberField(TEXT("capsule_radius_cm"), Player->GetCapsuleComponent()->GetScaledCapsuleRadius());
        Json->SetNumberField(TEXT("capsule_half_height_cm"), Half);
        auto* Movement = Player->GetCharacterMovement();
        Json->SetNumberField(TEXT("max_walk_speed_cm_s"), Movement->MaxWalkSpeed);
        Json->SetNumberField(TEXT("jump_velocity_cm_s"), Movement->JumpZVelocity);
        Json->SetNumberField(TEXT("gravity_cm_s2"), Movement->GetGravityZ());
        Json->SetNumberField(TEXT("gravity_scale"), Movement->GravityScale);
        Json->SetNumberField(TEXT("max_step_height_cm"), Movement->MaxStepHeight);
        Json->SetNumberField(TEXT("walkable_floor_angle_deg"), Movement->GetWalkableFloorAngle());
        return Json;
    }
    FString Selector = Request.QueryParams.FindRef(TEXT("selector"));
    if (Selector.IsEmpty()) Selector = TEXT("*");
    TArray<FString> Ids;
    FString Error;
    if (!LCResolve(Selector, Transactions->GetCommittedEntities(), Player ? Player->GetActorLocation() : FVector::ZeroVector,
        Request.QueryParams.FindRef(TEXT("nearest")) == TEXT("true"), true, Ids, Error)) return LCRequestError(Error, Transactions->GetVersion());
    Json->SetNumberField(TEXT("count"), Ids.Num());
    Json->SetNumberField(TEXT("version"), Transactions->GetVersion());
    if (Request.QueryParams.FindRef(TEXT("count")) == TEXT("true")) return Json;
    int32 Limit = 256;
    if (Request.QueryParams.Contains(TEXT("limit")) && (!LexTryParseString(Limit, *Request.QueryParams.FindRef(TEXT("limit"))) || Limit < 1 || Limit > 16384))
        return LCRequestError(TEXT("limit must be 1..16384"), Transactions->GetVersion());
    TArray<FString> Fields;
    const FString RequestedFields = Request.QueryParams.FindRef(TEXT("fields"));
    if (!RequestedFields.IsEmpty()) RequestedFields.ParseIntoArray(Fields, TEXT(","), false);
    const TArray<FString> Allowed = {TEXT("id"), TEXT("template"), TEXT("transform"), TEXT("bounds"), TEXT("tags"), TEXT("group"), TEXT("version"), TEXT("source"), TEXT("surface"), TEXT("collision"), TEXT("lod"), TEXT("semantic")};
    for (const auto& Field : Fields) if (!Allowed.Contains(Field)) return LCRequestError(TEXT("Unknown entity field: ") + Field, Transactions->GetVersion());
    TArray<TSharedPtr<FJsonValue>> Values;
    for (int32 Index = 0; Index < FMath::Min(Ids.Num(), Limit); ++Index)
    {
        auto Entity = Transactions->GetCommittedEntities()[Ids[Index]].ToJson();
        if (!Fields.IsEmpty()) for (const auto& Field : Allowed) if (Field != TEXT("id") && !Fields.Contains(Field)) Entity->RemoveField(Field);
        Values.Add(MakeShared<FJsonValueObject>(Entity));
    }
    Json->SetArrayField(TEXT("entities"), Values);
    Json->SetBoolField(TEXT("truncated"), Ids.Num() > Limit);
    return Json;
}
