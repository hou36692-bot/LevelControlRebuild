#include "LCTxService.h"
#include "LCSubsystem.h"
#include "Engine/Engine.h"
#include "Engine/GameViewportClient.h"
#include "Engine/World.h"
#include "Misc/AutomationTest.h"

#if WITH_DEV_AUTOMATION_TESTS
class FLCW4Smoke : public IAutomationLatentCommand
{
public:
    explicit FLCW4Smoke(FAutomationTestBase* InTest) : Test(InTest) {}
    virtual bool Update() override
    {
        auto* World = GEngine->GameViewport ? GEngine->GameViewport->GetWorld() : nullptr;
        if (!World) { Test->AddError(TEXT("Run with -game")); return true; }
        auto* Service = World->GetSubsystem<ULCSubsystem>()->GetTransactions();
        if (Started == 0) Started = FPlatformTime::Seconds();
        if (FPlatformTime::Seconds() - Started > 20) { Test->AddError(TEXT("W4 timeout")); return true; }
        if (!Waiting.IsEmpty())
        {
            const auto Receipt = Service->Receipt(Waiting);
            if (Receipt->GetStringField(TEXT("status")) == TEXT("scheduled")) return false;
            Test->TestEqual(TEXT("Action/undo applied"), Receipt->GetStringField(TEXT("status")), FString(TEXT("applied")));
            Test->TestTrue(TEXT("Full state consistent"), Service->Consistency()->GetBoolField(TEXT("ok")));
            Waiting.Reset();
        }
        if (Service->IsBusy()) return false;
        TSharedPtr<FJsonObject> Request = MakeShared<FJsonObject>();
        Request->SetStringField(TEXT("req_id"), FString::Printf(TEXT("w4-native-%d"), Step));
        if (Step == 0)
        {
            if (!Service->GetEntities().IsEmpty()) { Test->AddError(TEXT("Use an empty world")); return true; }
            Request = LCParseJson(TEXT(R"({"req_id":"w4-native-0","command":"create","operations":[{"op":"create","entity":{"id":"w4-column","template":"blockout/column","transform":{"position_cm":[1000,0,250],"size_cm":[100,100,500]}}}]})"));
            Service->Submit(Request); Waiting = Request->GetStringField(TEXT("req_id")); ++Step; return false;
        }
        if (Step >= 1 && Step <= 8)
        {
            if (Step % 2 == 0) { Request->SetBoolField(TEXT("last"), true); Service->Submit(Request, true); }
            else
            {
                const TCHAR* Kinds[] = {TEXT("spin"), TEXT("move"), TEXT("scale"), TEXT("oscillate")};
                Request->SetStringField(TEXT("selector"), TEXT("w4-column")); Request->SetStringField(TEXT("kind"), Kinds[(Step - 1) / 2]);
                Request->SetNumberField(TEXT("duration_s"), 0.1); Request->SetNumberField(TEXT("angle_deg"), 90);
                Request->SetNumberField(TEXT("factor"), 1.2); Request->SetNumberField(TEXT("frequency_hz"), 1);
                Request->SetArrayField(TEXT("delta_cm"), LCVector(FVector(100, 0, 0)));
                Request->SetArrayField(TEXT("amplitude_cm"), LCVector(FVector(0, 0, 20)));
                Service->Submit(Request, false, TEXT("action"));
            }
            Waiting = Request->GetStringField(TEXT("req_id")); ++Step; return false;
        }
        if (Step == 9)
        {
            const TCHAR* Kinds[] = {TEXT("spin"), TEXT("move"), TEXT("scale"), TEXT("oscillate")};
            for (const TCHAR* Kind : Kinds)
            {
                auto Bad = MakeShared<FJsonObject>(); Bad->SetStringField(TEXT("req_id"), FString(TEXT("bad-action-")) + Kind);
                Bad->SetStringField(TEXT("selector"), TEXT("w4-column")); Bad->SetStringField(TEXT("kind"), Kind); Bad->SetNumberField(TEXT("duration_s"), -1);
                Test->TestEqual(TEXT("Invalid action rejected"), Service->Submit(Bad, false, TEXT("action"))->GetStringField(TEXT("status")), FString(TEXT("rejected")));
            }
            auto Jump = Service->Check(LCParseJson(TEXT(R"({"kind":"jump","start_cm":[3000,0,0],"heading_deg":0})")));
            Test->TestTrue(TEXT("Jump collision landed"), Jump->GetBoolField(TEXT("landed")));
            Test->TestTrue(TEXT("Jump distance matches 3C"), Jump->GetNumberField(TEXT("horizontal_distance_cm")) > 490 && Jump->GetNumberField(TEXT("horizontal_distance_cm")) < 530);
            Test->TestFalse(TEXT("Invalid jump rejected"), Service->Check(LCParseJson(TEXT(R"({"kind":"jump","start_cm":[3000,0,0],"speed_cm_s":50000})")))->GetBoolField(TEXT("ok")));
            Test->TestTrue(TEXT("Capsule detects column"), Service->Check(LCParseJson(TEXT(R"({"kind":"clearance","from_cm":[800,0,0],"to_cm":[1200,0,0]})")))->GetBoolField(TEXT("blocked")));
            Test->TestFalse(TEXT("Open corridor clear"), Service->Check(LCParseJson(TEXT(R"({"kind":"clearance","from_cm":[0,500,0],"to_cm":[500,500,0]})")))->GetBoolField(TEXT("blocked")));
            auto Configure = Service->PlayerControl(LCParseJson(TEXT(R"({"req_id":"native-configure","action":"configure","max_walk_speed_cm_s":500})")));
            Test->TestEqual(TEXT("Configure player"), Configure->GetStringField(TEXT("status")), FString(TEXT("applied")));
            Test->TestEqual(TEXT("Bad player configuration rejected"), Service->PlayerControl(LCParseJson(TEXT(R"({"req_id":"native-bad-configure","action":"configure","gravity_scale":-1})")))->GetStringField(TEXT("status")), FString(TEXT("rejected")));
            Request->SetBoolField(TEXT("last"), true); Service->Submit(Request, true); Waiting = Request->GetStringField(TEXT("req_id")); ++Step; return false;
        }
        Test->TestEqual(TEXT("W4 leaves empty world"), Service->GetEntities().Num(), 0);
        return true;
    }
private:
    FAutomationTestBase* Test;
    int32 Step = 0;
    double Started = 0;
    FString Waiting;
};

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FLCW4Test, "LC.W4.ActionsAnd3C",
    EAutomationTestFlags::ClientContext | EAutomationTestFlags::EngineFilter)
bool FLCW4Test::RunTest(const FString& Parameters)
{
    ADD_LATENT_AUTOMATION_COMMAND(FLCW4Smoke(this)); return true;
}
#endif
