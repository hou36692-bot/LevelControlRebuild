#include "LCPresentation.h"
#include "LCWireframeComponent.h"
#include "Engine/Engine.h"
#include "Engine/GameViewportClient.h"
#include "Engine/World.h"
#include "Components/SceneComponent.h"
#include "Materials/MaterialInstanceDynamic.h"
#include "Misc/AutomationTest.h"
#include "HAL/PlatformProcess.h"
#include "Kismet/GameplayStatics.h"
#include "GameFramework/PlayerController.h"

#if WITH_DEV_AUTOMATION_TESTS
IMPLEMENT_SIMPLE_AUTOMATION_TEST(FLCUpwardGrowth, "LC.Presentation.UpwardGrowth",
    EAutomationTestFlags::ClientContext | EAutomationTestFlags::EngineFilter)

bool FLCUpwardGrowth::RunTest(const FString& Parameters)
{
    auto* World = GEngine->GameViewport ? GEngine->GameViewport->GetWorld() : nullptr;
    if (!World) { AddError(TEXT("Run in an isolated -game world")); return false; }
    auto* Host = World->SpawnActor<AActor>();
    auto* Root = NewObject<USceneComponent>(Host); Host->SetRootComponent(Root); Root->RegisterComponent();
    auto* Presentation = NewObject<ULCPresentation>(Host);
    TestTrue(TEXT("Initialized"), Presentation->Initialize(Host));
    FLCEntity A; A.Id = TEXT("growth-a"); A.Template = TEXT("blockout/box"); A.Group = TEXT("body");
    A.Position = FVector(2000, 0, 150); A.Size = FVector(100, 100, 300);
    FLCEntity B = A; B.Id = TEXT("growth-b"); B.Position.X += 200;
    auto Request = LCParseJson(TEXT(R"({"source":"growth-test","revision":1,"regions":[{"id":"body","center_cm":[2100,0,150],"size_cm":[400,200,300]}],"growth_duration_s":0.4,"timeline":[{"id":"growth-a","start_s":0,"duration_s":0.4},{"id":"growth-b","start_s":0,"duration_s":0.4}]})"));
    Request->SetArrayField(TEXT("entities"), {MakeShared<FJsonValueObject>(A.ToJson()), MakeShared<FJsonValueObject>(B.ToJson())});
    FString Error;
    TestTrue(TEXT("Published"), Presentation->Publish(Request, Error));
    for (int32 I = 0; I < 10; ++I) Presentation->Tick(0.016f);
    FPlatformProcess::Sleep(0.15f); Presentation->Tick(0.15f);
    auto Status = Presentation->Status();
    const auto& Groups = Status->GetArrayField(TEXT("growth_groups"));
    TestEqual(TEXT("One common height plane"), Groups.Num(), 1);
    const double Height = Groups[0]->AsObject()->GetNumberField(TEXT("height_cm"));
    TestTrue(TEXT("Height is inside geometry"), Height > 0 && Height < 300);
    TArray<UInstancedStaticMeshComponent*> Batches; Host->GetComponents(Batches);
    TestEqual(TEXT("One shared batch"), Batches.Num(), 1);
    if (!Batches.IsEmpty())
    {
        TestEqual(TEXT("All geometry prepared together"), Batches[0]->GetInstanceCount(), 2);
        FTransform First, Second; Batches[0]->GetInstanceTransform(0, First, true); Batches[0]->GetInstanceTransform(1, Second, true);
        TestTrue(TEXT("First transform never scales or moves"), First.Equals(A.Transform()));
        TestTrue(TEXT("Second transform never scales or moves"), Second.Equals(B.Transform()));
        auto* Material = Cast<UMaterialInstanceDynamic>(Batches[0]->GetMaterial(0));
        TestNotNull(TEXT("Preview uses dynamic clipping material"), Material);
        if (Material) TestTrue(TEXT("Material uses common world height"), FMath::Abs(Material->K2_GetScalarParameterValue(TEXT("LCRevealHeightCM")) - Height) < 0.01);
    }
    TArray<ULCWireframeComponent*> Wires; Host->GetComponents(Wires);
    TestTrue(TEXT("Wireframe height is clipped too"), !Wires.IsEmpty() && Wires[0]->GetHeights().Num() == 1 && Wires[0]->GetHeights()[0] < 300);
    FLCEntity C = A; C.Id = TEXT("growth-roof"); C.Group = TEXT("roof"); C.Position.Z = 350; C.Size.Z = 100;
    Request->SetNumberField(TEXT("revision"), 2); Request->SetBoolField(TEXT("incremental"), true);
    Request->SetArrayField(TEXT("entities"), {MakeShared<FJsonValueObject>(A.ToJson()), MakeShared<FJsonValueObject>(B.ToJson()), MakeShared<FJsonValueObject>(C.ToJson())});
    auto Times = Request->GetArrayField(TEXT("timeline"));
    Times.Add(MakeShared<FJsonValueObject>(LCParseJson(TEXT(R"({"id":"growth-roof","start_s":0,"duration_s":0.4})"))));
    Request->SetArrayField(TEXT("timeline"), Times);
    TestTrue(TEXT("Incremental publish"), Presentation->Publish(Request, Error));
    Presentation->Tick(.016f);
    TestEqual(TEXT("Old instances remain in their original batch"), Batches[0]->GetInstanceCount(), 2);
    const double OldHeight = CastChecked<UMaterialInstanceDynamic>(Batches[0]->GetMaterial(0))->K2_GetScalarParameterValue(TEXT("LCRevealHeightCM"));
    TestTrue(TEXT("Existing height never restarts"), OldHeight >= Height);
    TestFalse(TEXT("Duplicate revision rejected"), Presentation->Publish(Request, Error));
    Presentation->Skip();
    for (int32 I = 0; I < 10; ++I) Presentation->Tick(.016f);
    TestTrue(TEXT("Skip reveals complete geometry"), Presentation->IsSettled());
    Presentation->Handoff(A.Id);
    TestEqual(TEXT("Real-instance handoff removes only its preview"), Batches[0]->GetInstanceCount(), 1);
    TestEqual(TEXT("Handoff counted"), Presentation->Status()->GetNumberField(TEXT("handoff_count")), 1.0);
    Presentation->Cancel();
    TestTrue(TEXT("Cancel terminates preview"), Presentation->IsCancelled());
    Request->SetNumberField(TEXT("revision"), 3); Request->SetBoolField(TEXT("incremental"), false);
    Request->SetBoolField(TEXT("director"), true);
    Request->SetObjectField(TEXT("cameras"), LCParseJson(TEXT(R"({"overview":{"position_cm":[1500,-1600,900],"target_cm":[2100,0,150]},"hero":{"position_cm":[1500,-1600,900],"target_cm":[2100,0,150]},"groups":{}})")));
    TestTrue(TEXT("Director starts"), Presentation->Publish(Request, Error));
    auto* Player = UGameplayStatics::GetPlayerController(World, 0);
    if (Player)
    {
        const FRotator Original = Player->GetControlRotation();
        Player->SetControlRotation(Original + FRotator(0, 5, 0));
        Presentation->Tick(.016f);
        TestEqual(TEXT("Look input returns camera"), Presentation->Status()->GetStringField(TEXT("camera")), FString(TEXT("player")));
        TestFalse(TEXT("Look input does not skip construction"), Presentation->Status()->GetBoolField(TEXT("instant")));
        Request->SetNumberField(TEXT("revision"), 4); Request->SetBoolField(TEXT("incremental"), true);
        TestTrue(TEXT("Next delivery accepted after camera return"), Presentation->Publish(Request, Error));
        TestEqual(TEXT("Next delivery does not steal camera"), Presentation->Status()->GetStringField(TEXT("camera")), FString(TEXT("player")));
        Player->SetControlRotation(Original);
    }
    Presentation->Cancel();
    Request->SetNumberField(TEXT("revision"), 5); Request->SetBoolField(TEXT("incremental"), false);
    auto LongTimes = Request->GetArrayField(TEXT("timeline"));
    for (const auto& Value : LongTimes) Value->AsObject()->SetNumberField(TEXT("duration_s"), 12);
    Request->SetArrayField(TEXT("timeline"), LongTimes);
    TestTrue(TEXT("Legacy long timeline accepted"), Presentation->Publish(Request, Error));
    Presentation->Cancel();
    Host->Destroy();
    return true;
}
#endif
