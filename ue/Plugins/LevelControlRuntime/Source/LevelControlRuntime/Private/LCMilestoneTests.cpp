#include "LCTxService.h"
#include "LCSubsystem.h"
#include "Engine/Engine.h"
#include "Engine/GameViewportClient.h"
#include "Engine/World.h"
#include "Misc/AutomationTest.h"

#if WITH_DEV_AUTOMATION_TESTS
class FLCMilestoneSmoke : public IAutomationLatentCommand
{
public:
    explicit FLCMilestoneSmoke(FAutomationTestBase* InTest) : Test(InTest) {}
    virtual bool Update() override
    {
        auto* World = GEngine->GameViewport ? GEngine->GameViewport->GetWorld() : nullptr;
        if (!World) { Test->AddError(TEXT("Run with -game")); return true; }
        auto* Service = World->GetSubsystem<ULCSubsystem>()->GetTransactions();
        if (!Started) Started = FPlatformTime::Seconds();
        if (FPlatformTime::Seconds() - Started > 20) { Test->AddError(TEXT("W2/W3 automation timeout")); return true; }
        if (!WaitingId.IsEmpty())
        {
            auto Receipt = Service->Receipt(WaitingId);
            if (Receipt->GetStringField(TEXT("status")) == TEXT("scheduled")) return false;
            Test->TestEqual(TEXT("Transaction applied"), Receipt->GetStringField(TEXT("status")), FString(TEXT("applied")));
            Test->TestTrue(TEXT("Registry and SQLite match"), Service->Consistency()->GetBoolField(TEXT("ok")));
            WaitingId.Reset();
        }
        if (Service->IsBusy()) return false;
        TSharedPtr<FJsonObject> Request = MakeShared<FJsonObject>();
        Request->SetStringField(TEXT("req_id"), FString::Printf(TEXT("w23-%d"), Step));
        const FString RequestId = Request->GetStringField(TEXT("req_id"));
        switch (Step++)
        {
        case 0:
        {
            if (!Service->GetEntities().IsEmpty()) { Test->AddError(TEXT("Use an empty test world")); return true; }
            FLCEntity Entity; Entity.Id = TEXT("test-court-01-01-00"); Entity.Template = TEXT("blockout/column");
            Entity.Source = TEXT("test-court"); Entity.Position = FVector(1500, 0, 250); Entity.Size = FVector(100, 100, 500);
            Entities.Add(MakeShared<FJsonValueObject>(Entity.ToJson()));
            Preview = LCParseJson(TEXT(R"({"req_id":"w23-preview","source":"test-court","revision":100,"regions":[],"timeline":[{"id":"test-court-01-01-00","start_s":2,"duration_s":0.1}]})"));
            Preview->SetArrayField(TEXT("entities"), Entities);
            Test->TestEqual(TEXT("Preview accepted"), Service->Preview(Preview)->GetStringField(TEXT("status")), FString(TEXT("applied")));
            Test->TestEqual(TEXT("Preview is absent from registry"), Service->GetEntities().Num(), 0);
            auto Invalid = LCParseJson(LCJsonString(Preview)); Invalid->SetStringField(TEXT("req_id"), TEXT("invalid-preview"));
            Test->TestEqual(TEXT("Stale revision rejected"), Service->Preview(Invalid)->GetStringField(TEXT("status")), FString(TEXT("rejected")));
            break;
        }
        case 1:
            Request->SetStringField(TEXT("source"), TEXT("test-court")); Request->SetNumberField(TEXT("revision"), 100);
            Request->SetStringField(TEXT("mode"), TEXT("reconcile")); Request->SetNumberField(TEXT("expected_version"), Service->GetVersion());
            Request->SetArrayField(TEXT("entities"), Entities);
            Test->TestEqual(TEXT("Commit queued"), Service->Commit(Request)->GetStringField(TEXT("status")), FString(TEXT("scheduled")));
            break;
        case 2:
            Request->SetStringField(TEXT("action"), TEXT("cancel"));
            Test->TestEqual(TEXT("Cancellation accepted"), Service->Control(Request)->GetStringField(TEXT("status")), FString(TEXT("applied")));
            Test->TestEqual(TEXT("Pending commit cancelled"), Service->Receipt(TEXT("w23-1"))->GetStringField(TEXT("status")), FString(TEXT("rejected")));
            Test->TestEqual(TEXT("No entity rollback needed"), Service->GetEntities().Num(), 0);
            break;
        case 3:
            Preview->SetStringField(TEXT("req_id"), TEXT("w23-preview-new")); Preview->SetNumberField(TEXT("revision"), 101);
            Preview->GetArrayField(TEXT("timeline"))[0]->AsObject()->SetNumberField(TEXT("start_s"), 0);
            Test->TestEqual(TEXT("Replacement preview accepted"), Service->Preview(Preview)->GetStringField(TEXT("status")), FString(TEXT("applied")));
            Request->SetStringField(TEXT("source"), TEXT("test-court")); Request->SetNumberField(TEXT("revision"), 101);
            Request->SetStringField(TEXT("mode"), TEXT("reconcile")); Request->SetNumberField(TEXT("expected_version"), Service->GetVersion());
            Request->SetArrayField(TEXT("entities"), Entities);
            Service->Commit(Request); WaitingId = RequestId; break;
        case 4:
            Test->TestEqual(TEXT("Formal entity created"), Service->GetEntities().Num(), 1);
            Request->SetStringField(TEXT("action"), TEXT("save")); Request->SetStringField(TEXT("name"), TEXT("w23-built"));
            Service->Submit(Request, false, TEXT("checkpoint")); WaitingId = RequestId; break;
        case 5:
            Request->SetStringField(TEXT("action"), TEXT("save")); Request->SetStringField(TEXT("name"), TEXT("w23-built"));
            Test->TestEqual(TEXT("Duplicate checkpoint rejected"), Service->Submit(Request, false, TEXT("checkpoint"))->GetStringField(TEXT("status")), FString(TEXT("rejected")));
            break;
        case 6:
            Request = LCParseJson(TEXT(R"({"req_id":"w23-6","command":"delete","operations":[{"op":"delete","selector":"test-court-*"}]})"));
            Service->Submit(Request); WaitingId = RequestId; break;
        case 7:
            Request->SetStringField(TEXT("action"), TEXT("restore")); Request->SetStringField(TEXT("name"), TEXT("w23-built"));
            Service->Submit(Request, false, TEXT("checkpoint")); WaitingId = RequestId; break;
        case 8:
            Test->TestEqual(TEXT("Checkpoint restored"), Service->GetEntities().Num(), 1);
            Request->SetBoolField(TEXT("last"), true); Service->Submit(Request, true); WaitingId = RequestId; break;
        case 9:
            Test->TestEqual(TEXT("Undo restore returns empty"), Service->GetEntities().Num(), 0);
            Request->SetStringField(TEXT("action"), TEXT("restore")); Request->SetStringField(TEXT("name"), TEXT("missing"));
            Test->TestEqual(TEXT("Unknown checkpoint rejected"), Service->Submit(Request, false, TEXT("checkpoint"))->GetStringField(TEXT("status")), FString(TEXT("rejected")));
            Request->SetStringField(TEXT("req_id"), TEXT("w23-end")); Request->SetStringField(TEXT("action"), TEXT("cancel")); Service->Control(Request);
            return true;
        }
        return false;
    }
private:
    FAutomationTestBase* Test;
    int32 Step = 0;
    double Started = 0;
    FString WaitingId;
    TSharedPtr<FJsonObject> Preview;
    TArray<TSharedPtr<FJsonValue>> Entities;
};

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FLCMilestoneTest, "LC.W23.PreviewCommitCheckpoint",
    EAutomationTestFlags::ClientContext | EAutomationTestFlags::EngineFilter)
bool FLCMilestoneTest::RunTest(const FString& Parameters)
{
    ADD_LATENT_AUTOMATION_COMMAND(FLCMilestoneSmoke(this));
    return true;
}
#endif
