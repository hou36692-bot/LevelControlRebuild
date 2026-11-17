#include "LCTxService.h"
#include "LCSubsystem.h"
#include "Engine/Engine.h"
#include "Engine/GameViewportClient.h"
#include "Engine/World.h"
#include "Misc/AutomationTest.h"
#include "Misc/Paths.h"
#include "Kismet/GameplayStatics.h"
#include "GameFramework/Character.h"
#include "LCSelector.h"

#if WITH_DEV_AUTOMATION_TESTS
IMPLEMENT_SIMPLE_AUTOMATION_TEST(FLCTemplateTest, "LC.Step3.Templates",
    EAutomationTestFlags::ClientContext | EAutomationTestFlags::EngineFilter)

bool FLCTemplateTest::RunTest(const FString& Parameters)
{
    auto* World = GEngine->GameViewport ? GEngine->GameViewport->GetWorld() : nullptr;
    if (!World) { AddError(TEXT("Run with -game")); return false; }
    auto* Service = World->GetSubsystem<ULCSubsystem>()->GetTransactions();
    const int32 Before = Service->GetInstanceCount();
    int32 Index = 0;
    for (const auto& Template : LCTemplates())
    {
        FLCEntity Entity;
        Entity.Id = FString::Printf(TEXT("smoke-template-%d"), Index++);
        Entity.Template = Template.Id;
        Entity.Size = Template.Size;
        Entity.Position = FVector(1000, Index * 600, Entity.Size.Z * 0.5);
        FString Handle, Error;
        TestTrue(TEXT("Template created"), Service->Create(Entity, Handle, Error));
        TestEqual(TEXT("Stable entity handle"), Handle, Entity.Id);
        TestEqual(TEXT("Full bounds height"), Entity.Bounds().GetSize().Z, Entity.Size.Z);
        TestFalse(TEXT("Duplicate rejected"), Service->Create(Entity, Handle, Error));
    }
    TestEqual(TEXT("ISM instance count"), Service->GetInstanceCount(), Before + LCTemplates().Num());
    for (int32 Item = 0; Item < LCTemplates().Num(); ++Item) TestTrue(TEXT("Instance removed by stable handle"), Service->Remove(FString::Printf(TEXT("smoke-template-%d"), Item)));
    TestEqual(TEXT("No leaked instances"), Service->GetInstanceCount(), Before);
    return true;
}
IMPLEMENT_SIMPLE_AUTOMATION_TEST(FLCStoreTest, "LC.Step4.StoreAtomicity",
    EAutomationTestFlags::ClientContext | EAutomationTestFlags::EngineFilter)

bool FLCStoreTest::RunTest(const FString& Parameters)
{
    FLCStore Store;
    const FString Path = FPaths::ProjectSavedDir() / TEXT("Tests") / (FGuid::NewGuid().ToString() + TEXT(".sqlite3"));
    if (!TestTrue(TEXT("Open SQLite"), Store.Open(Path))) return false;
    FLCEntity Entity;
    Entity.Id = TEXT("store-test"); Entity.Template = TEXT("blockout/column"); Entity.Version = 1;
    FLCStoreEdit Edit;
    Edit.RequestId = TEXT("store-create"); Edit.TransactionId = TEXT("tx-store-create"); Edit.Version = 1;
    Edit.Receipt = MakeShared<FJsonObject>(); Edit.Receipt->SetStringField(TEXT("status"), TEXT("applied"));
    Edit.Changes.Add({Entity.Id, nullptr, Entity.ToJson()});
    TestTrue(TEXT("Commit create"), Store.Commit(Edit));
    TestFalse(TEXT("Database enforces request id uniqueness"), Store.Commit(Edit));
    Store.Close();
    TestTrue(TEXT("Reopen durable database"), Store.Open(Path));
    TMap<FString, FLCEntity> Entities;
    TMap<FString, TSharedPtr<FJsonObject>> Receipts;
    int64 Version;
    TestTrue(TEXT("Restore registry"), Store.Load(Entities, Receipts, Version));
    TestEqual(TEXT("Persisted entity"), Entities.Num(), 1);
    TestEqual(TEXT("Persisted version"), Version, int64(1));
    FLCStoreEdit Bad = Edit;
    Bad.RequestId = TEXT("bad"); Bad.TransactionId = TEXT("tx-bad"); Bad.Version = 2;
    Bad.Changes[0].After = nullptr;
    auto Invalid = Entity.ToJson(); Invalid->SetStringField(TEXT("template"), TEXT("missing"));
    Bad.Changes.Add({TEXT("invalid"), nullptr, Invalid});
    TestFalse(TEXT("Foreign key violation rejected"), Store.Commit(Bad));
    TestTrue(TEXT("Reload after failed transaction"), Store.Load(Entities, Receipts, Version));
    TestTrue(TEXT("Earlier deletion rolled back"), Entities.Contains(Entity.Id));
    FLCStoreEdit Last;
    TestTrue(TEXT("Undo history"), Store.LatestEdit(Last));
    FLCStoreEdit Undo;
    Undo.RequestId = TEXT("undo"); Undo.TransactionId = TEXT("tx-undo"); Undo.Kind = TEXT("undo");
    Undo.UndoTarget = Last.TransactionId; Undo.Version = 2; Undo.Receipt = Edit.Receipt;
    Undo.Changes.Add({Entity.Id, Entity.ToJson(), nullptr});
    TestTrue(TEXT("Commit undo"), Store.Commit(Undo));
    TestTrue(TEXT("Reload after undo"), Store.Load(Entities, Receipts, Version));
    TestEqual(TEXT("Undo restored empty registry"), Entities.Num(), 0);
    TestFalse(TEXT("Undone edit not repeated"), Store.LatestEdit(Last));
    Store.Close();
    return true;
}
class FLCTransactionSmoke : public IAutomationLatentCommand
{
public:
    explicit FLCTransactionSmoke(FAutomationTestBase* InTest) : Test(InTest) {}
    virtual bool Update() override
    {
        if (!Service.IsValid())
        {
            auto* World = GEngine->GameViewport ? GEngine->GameViewport->GetWorld() : nullptr;
            if (!World) { Test->AddError(TEXT("Run with -game")); return true; }
            Service = World->GetSubsystem<ULCSubsystem>()->GetTransactions();
            if (!Service.IsValid() || !Service->GetEntities().IsEmpty()) { Test->AddError(TEXT("Use an empty test database")); return true; }
            const TCHAR* Cases[] = {
                TEXT(R"({"command":"create","operations":[{"op":"create","entity":{"id":"tx-test","template":"blockout/column","transform":{"position_cm":[1000,0,250],"size_cm":[100,100,500]}}}]})"),
                TEXT(R"({"command":"duplicate","operations":[{"op":"create","entity":{"id":"tx-test","template":"blockout/column","transform":{"position_cm":[1000,0,250],"size_cm":[100,100,500]}}}]})"),
                TEXT(R"({"command":"move","operations":[{"op":"move","selector":"tx-test","delta_cm":[200,0,0]}]})"),
                TEXT(R"({"command":"move missing","operations":[{"op":"move","selector":"missing","delta_cm":[200,0,0]}]})"),
                TEXT(R"({"command":"set","operations":[{"op":"set","selector":"tx-test","transform":{"size_cm":[100,100,600]}}]})"),
                TEXT(R"({"command":"set invalid","operations":[{"op":"set","selector":"tx-test","transform":{"size_cm":[100,100,-1]}}]})"),
                TEXT(R"({"command":"delete","operations":[{"op":"delete","selector":"tx-test"}]})"),
                TEXT(R"({"command":"delete missing","operations":[{"op":"delete","selector":"tx-test"}]})"),
                TEXT(R"({"last":true})"), TEXT(R"({"last":true})"), TEXT(R"({"last":true})"), TEXT(R"({"last":true})"), TEXT(R"({"last":true})")
            };
            Expected = {true, false, true, false, true, false, true, false, true, true, true, true, false};
            for (const TCHAR* Item : Cases) Requests.Add(LCParseJson(Item));
            FLCEntity Overlap;
            Overlap.Id = TEXT("capsule-test"); Overlap.Template = TEXT("blockout/box");
            Overlap.Position = UGameplayStatics::GetPlayerCharacter(World, 0)->GetActorLocation();
            auto Operation = MakeShared<FJsonObject>(); Operation->SetStringField(TEXT("op"), TEXT("create")); Operation->SetObjectField(TEXT("entity"), Overlap.ToJson());
            auto Request = MakeShared<FJsonObject>(); Request->SetStringField(TEXT("command"), TEXT("overlap")); Request->SetArrayField(TEXT("operations"), {MakeShared<FJsonValueObject>(Operation)});
            Requests.Add(Request); Expected.Add(false);
            auto Oversized = MakeShared<FJsonObject>(); Oversized->SetStringField(TEXT("command"), TEXT("oversized"));
            TArray<TSharedPtr<FJsonValue>> Operations;
            Operations.Init(MakeShared<FJsonValueObject>(Operation), 16385);
            Oversized->SetArrayField(TEXT("operations"), Operations);
            Requests.Add(Oversized); Expected.Add(false);
        }
        if (Index == Requests.Num())
        {
            Test->TestTrue(TEXT("Registry and SQLite agree"), Service->Consistency()->GetBoolField(TEXT("ok")));
            Test->TestEqual(TEXT("Undo chain restored empty state"), Service->GetEntities().Num(), 0);
            return true;
        }
        const FString Id = FString::Printf(TEXT("automation-%d"), Index);
        if (!Waiting)
        {
            Requests[Index]->SetStringField(TEXT("req_id"), Id);
            auto First = Service->Submit(Requests[Index], Requests[Index]->HasField(TEXT("last")));
            auto Replay = Service->Submit(Requests[Index], Requests[Index]->HasField(TEXT("last")));
            Test->TestEqual(TEXT("Request replay is idempotent"), LCJsonString(First), LCJsonString(Replay));
            Waiting = true; Started = FPlatformTime::Seconds();
        }
        auto Receipt = Service->Receipt(Id);
        if (Receipt->GetStringField(TEXT("status")) == TEXT("scheduled"))
        {
            if (FPlatformTime::Seconds() - Started > 10) { Test->AddError(TEXT("Transaction timed out")); return true; }
            return false;
        }
        const FString Status = Expected[Index] ? TEXT("applied") : TEXT("rejected");
        Test->TestEqual(FString::Printf(TEXT("Case %d status"), Index), Receipt->GetStringField(TEXT("status")), Status);
        if (Receipt->GetStringField(TEXT("status")) != Status) Test->AddInfo(LCJsonString(Receipt));
        Test->TestTrue(TEXT("Consistent after every terminal receipt"), Service->Consistency()->GetBoolField(TEXT("ok")));
        if (Index == 0) Test->TestEqual(TEXT("Created 5 m column"), Service->GetEntities()[TEXT("tx-test")].Size.Z, 500.0);
        ++Index; Waiting = false;
        return false;
    }
private:
    FAutomationTestBase* Test;
    TWeakObjectPtr<ULCTxService> Service;
    TArray<TSharedPtr<FJsonObject>> Requests;
    TArray<bool> Expected;
    int32 Index = 0;
    bool Waiting = false;
    double Started = 0;
};

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FLCTxTest, "LC.Step5.Transactions",
    EAutomationTestFlags::ClientContext | EAutomationTestFlags::EngineFilter)
bool FLCTxTest::RunTest(const FString& Parameters)
{
    ADD_LATENT_AUTOMATION_COMMAND(FLCTransactionSmoke(this));
    return true;
}
IMPLEMENT_SIMPLE_AUTOMATION_TEST(FLCSelectorTest, "LC.Step6.Selectors",
    EAutomationTestFlags::ClientContext | EAutomationTestFlags::EngineFilter)
bool FLCSelectorTest::RunTest(const FString& Parameters)
{
    TMap<FString, FLCEntity> Entities;
    for (int32 Index = 1; Index <= 3; ++Index)
    {
        FLCEntity Entity; Entity.Id = FString::Printf(TEXT("column-%d"), Index); Entity.Position.X = Index * 100;
        Entities.Add(Entity.Id, Entity);
    }
    TArray<FString> Ids;
    FString Error;
    TestTrue(TEXT("Glob"), LCResolve(TEXT("column-*"), Entities, FVector::ZeroVector, false, false, Ids, Error));
    TestEqual(TEXT("Glob count"), Ids.Num(), 3);
    TestTrue(TEXT("Union"), LCResolve(TEXT("column-3,column-1,column-1"), Entities, FVector::ZeroVector, false, false, Ids, Error));
    TestEqual(TEXT("Deduplicated union"), Ids.Num(), 2);
    TestTrue(TEXT("Nearest"), LCResolve(TEXT("column-*"), Entities, FVector::ZeroVector, true, false, Ids, Error));
    TestEqual(TEXT("Nearest stable id"), Ids[0], FString(TEXT("column-1")));
    TestFalse(TEXT("Empty term rejected"), LCResolve(TEXT("column-1,,column-2"), Entities, FVector::ZeroVector, false, false, Ids, Error));
    TestFalse(TEXT("Unknown id rejected"), LCResolve(TEXT("missing"), Entities, FVector::ZeroVector, false, false, Ids, Error));
    return true;
}

class FLCSelectionSmoke : public IAutomationLatentCommand
{
public:
    explicit FLCSelectionSmoke(FAutomationTestBase* InTest) : Test(InTest) {}
    virtual bool Update() override
    {
        auto* World = GEngine->GameViewport ? GEngine->GameViewport->GetWorld() : nullptr;
        if (!World) { Test->AddError(TEXT("Run with -game")); return true; }
        auto* Service = World->GetSubsystem<ULCSubsystem>()->GetTransactions();
        if (Step == 0)
        {
            if (!Service->GetEntities().IsEmpty()) { Test->AddError(TEXT("Use empty test database")); return true; }
            auto Request = LCParseJson(TEXT(R"({"req_id":"selection-create","command":"create selection demo","operations":[{"op":"create","entity":{"id":"selection-column","template":"blockout/column","transform":{"position_cm":[450,0,250],"size_cm":[100,100,500]}}}]})"));
            Service->Submit(Request); ++Step; Started = FPlatformTime::Seconds(); return false;
        }
        if (FPlatformTime::Seconds() - Started > 10) { Test->AddError(TEXT("Selection demo timeout")); return true; }
        if (Service->IsBusy()) return false;
        if (Step == 1)
        {
            auto Receipt = Service->Select(LCParseJson(TEXT(R"({"req_id":"selection-select","selector":"selection-*","nearest":true})")));
            Test->TestEqual(TEXT("Select applied"), Receipt->GetStringField(TEXT("status")), FString(TEXT("applied")));
            Test->TestEqual(TEXT("Selected one"), Receipt->GetIntegerField(TEXT("applied")), 1);
            ++Step; SelectedAt = FPlatformTime::Seconds(); return false;
        }
        if (Step == 2)
        {
            if (FPlatformTime::Seconds() - SelectedAt < 0.5) return false;
            FScreenshotRequest::RequestScreenshot(TEXT("step6-selection.png"), false, false);
            ++Step; return false;
        }
        if (Step == 3)
        {
            if (FScreenshotRequest::IsScreenshotRequested()) return false;
            Service->Submit(LCParseJson(TEXT(R"({"req_id":"selection-undo","last":true})")), true);
            ++Step; return false;
        }
        Test->TestEqual(TEXT("Select did not consume undo history"), Service->GetEntities().Num(), 0);
        Test->TestTrue(TEXT("Selection leaves registry consistent"), Service->Consistency()->GetBoolField(TEXT("ok")));
        return true;
    }
private:
    FAutomationTestBase* Test;
    int32 Step = 0;
    double Started = 0;
    double SelectedAt = 0;
};

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FLCSelectionTest, "LC.Step6.Wireframe",
    EAutomationTestFlags::ClientContext | EAutomationTestFlags::EngineFilter)
bool FLCSelectionTest::RunTest(const FString& Parameters)
{
    ADD_LATENT_AUTOMATION_COMMAND(FLCSelectionSmoke(this));
    return true;
}
#endif
