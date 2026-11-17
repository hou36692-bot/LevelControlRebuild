#include "LCEnvironment.h"
#include "LCSurface.h"
#include "LCTypes.h"
#include "LCStore.h"
#include "Misc/AutomationTest.h"
#include "Misc/Paths.h"
#include "HAL/FileManager.h"

#if WITH_DEV_AUTOMATION_TESTS
IMPLEMENT_SIMPLE_AUTOMATION_TEST(FLCEnvironmentParametersTest, "LC.V2.EnvironmentParameters",
    EAutomationTestFlags::ClientContext | EAutomationTestFlags::EngineFilter)
bool FLCEnvironmentParametersTest::RunTest(const FString& Parameters)
{
    TestEqual(TEXT("Six presets"), ULCEnvironment::Presets()->Values.Num(), 6);
    for (const auto& Item : ULCEnvironment::Presets()->Values)
    {
        TSharedPtr<FJsonObject> Result; FString Error;
        TestTrue(TEXT("Preset validates"), ULCEnvironment::Patch(ULCEnvironment::Defaults(), Item.Value->AsObject(), Result, Error));
    }
    const TCHAR* Invalid[] = {TEXT("{\"time_h\":25}"), TEXT("{\"sun_intensity\":-1}"), TEXT("{\"sun_temperature_k\":90000}"),
        TEXT("{\"fog_density\":0.2}"), TEXT("{\"fog_color\":[1,2,3]}"), TEXT("{\"cloud_coverage\":2}"),
        TEXT("{\"rain\":1}"), TEXT("{\"time_h\":\"12\"}"), TEXT("{\"time_h\":true}"), TEXT("{\"contrast\":null}"),
        TEXT("{\"fog_color\":[0,0]}"), TEXT("{\"fog_color\":\"red\"}"), TEXT("{\"fog_falloff\":0}"),
        TEXT("{\"atmosphere_density\":-1}"), TEXT("{\"exposure_ev\":11}"), TEXT("{\"saturation\":3}")};
    for (const auto* Text : Invalid)
    {
        TSharedPtr<FJsonObject> Result; FString Error;
        TestFalse(TEXT("Invalid patch rejected"), ULCEnvironment::Patch(ULCEnvironment::Defaults(), LCParseJson(Text), Result, Error));
        TestFalse(TEXT("Rejection explains why"), Error.IsEmpty());
    }
    TSharedPtr<FJsonObject> Result; FString Error;
    TestTrue(TEXT("Explicit elevation overrides time"), ULCEnvironment::Patch(ULCEnvironment::Defaults(), LCParseJson(TEXT("{\"time_h\":12,\"sun_elevation_deg\":30}")), Result, Error));
    TestEqual(TEXT("Elevation retained"), Result->GetNumberField(TEXT("sun_elevation_deg")), 30.0);
    const FString OriginalSurface = LCJsonString(LCSurfacePresets());
    TestTrue(TEXT("Default surface patch"), LCSurfacePatch(nullptr, TEXT(""), LCParseJson(TEXT("{\"roughness\":0.13}")), Result, Error));
    TestEqual(TEXT("Surface defaults remain immutable"), LCJsonString(LCSurfacePresets()), OriginalSurface);
    TestTrue(TEXT("Named surface patch"), LCSurfacePatch(nullptr, TEXT("metal-steel"), LCParseJson(TEXT("{\"roughness\":0.27}")), Result, Error));
    TestEqual(TEXT("Named presets remain immutable"), LCJsonString(LCSurfacePresets()), OriginalSurface);
    return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FLCEnvironmentStoreTest, "LC.V2.EnvironmentStore",
    EAutomationTestFlags::ClientContext | EAutomationTestFlags::EngineFilter)
bool FLCEnvironmentStoreTest::RunTest(const FString& Parameters)
{
    FLCStore Store; TestTrue(TEXT("Open store"), Store.Open(TEXT(":memory:")));
    auto State = MakeShared<FJsonObject>(); State->SetObjectField(TEXT("values"), ULCEnvironment::Defaults());
    State->SetObjectField(TEXT("presets"), MakeShared<FJsonObject>());
    FLCStoreEdit Edit; Edit.RequestId = TEXT("environment-store"); Edit.TransactionId = TEXT("environment-tx");
    Edit.Command = TEXT("environment apply"); Edit.Version = 1; Edit.Receipt = LCParseJson(TEXT("{\"status\":\"applied\"}"));
    Edit.Changes.Add({TEXT("@environment"), State, State});
    Edit.CheckpointName = TEXT("environment-checkpoint");
    auto Snapshot = MakeShared<FJsonObject>(); Snapshot->SetObjectField(TEXT("@environment"), State);
    Edit.CheckpointSnapshot = LCJsonString(Snapshot);
    TestTrue(TEXT("World and receipt atomic commit"), Store.Commit(Edit));
    TMap<FString, FLCEntity> Entities; TMap<FString, TSharedPtr<FJsonObject>> Receipts; int64 Version;
    TSharedPtr<FJsonObject> Environment;
    TestTrue(TEXT("Read world"), Store.Load(Entities, Receipts, Version, &Environment));
    TestEqual(TEXT("No fake environment entity"), Entities.Num(), 0);
    TestEqual(TEXT("State exact"), LCJsonString(Environment), LCJsonString(State));
    FLCStoreEdit Latest; TestTrue(TEXT("Undo history"), Store.LatestEdit(Latest));
    TestEqual(TEXT("Undo kind"), Latest.Changes[0].Id, FString(TEXT("@environment")));
    Environment.Reset();
    TestTrue(TEXT("Checkpoint world"), Store.Checkpoint(TEXT("environment-checkpoint"), Entities, &Environment));
    TestEqual(TEXT("Checkpoint exact"), LCJsonString(Environment), LCJsonString(State));
    FLCStoreEdit Failed;
    Failed.RequestId = TEXT("combined-failure"); Failed.TransactionId = TEXT("combined-failure-tx");
    Failed.Version = 2; Failed.Receipt = Edit.Receipt;
    auto Changed = LCParseJson(LCJsonString(State));
    Changed->SetObjectField(TEXT("values"), ULCEnvironment::Presets()->GetObjectField(TEXT("night")));
    Failed.Changes.Add({TEXT("@environment"), State, Changed});
    FLCEntity Invalid; Invalid.Id = TEXT("invalid-geometry"); Invalid.Template = TEXT("missing");
    Failed.Changes.Add({Invalid.Id, nullptr, Invalid.ToJson()});
    TestFalse(TEXT("Combined geometry failure rejected"), Store.Commit(Failed));
    TestTrue(TEXT("Reload combined failure"), Store.Load(Entities, Receipts, Version, &Environment));
    TestEqual(TEXT("Environment rolled back with geometry"), LCJsonString(Environment), LCJsonString(State));
    TestEqual(TEXT("No partial geometry persisted"), Entities.Num(), 0);
    TestEqual(TEXT("Combined failure preserves version"), Version, int64(1));
    TestFalse(TEXT("Failed request not reported applied"), Receipts.Contains(Failed.RequestId));
    Store.Close(); return true;
}
IMPLEMENT_SIMPLE_AUTOMATION_TEST(FLCEnvironmentMigrationTest, "LC.V2.LegacyMigration",
    EAutomationTestFlags::ClientContext | EAutomationTestFlags::EngineFilter)
bool FLCEnvironmentMigrationTest::RunTest(const FString& Parameters)
{
    const FString Path = FPaths::ProjectSavedDir() / TEXT("Tests") / (FGuid::NewGuid().ToString(EGuidFormats::Digits) + TEXT("-legacy.sqlite3"));
    FLCStore Store; TestTrue(TEXT("Create fixture"), Store.Open(Path));
    FLCEntity Entity; Entity.Id = TEXT("legacy-column"); Entity.Template = TEXT("blockout/column");
    Entity.Position = FVector(1000, 0, 250); Entity.Size = FVector(100, 100, 500); Entity.Version = 1;
    FLCStoreEdit Edit; Edit.RequestId = TEXT("legacy-create"); Edit.TransactionId = TEXT("legacy-tx");
    Edit.Command = TEXT("create"); Edit.Version = 1; Edit.Receipt = LCParseJson(TEXT("{\"status\":\"applied\"}"));
    Edit.Changes.Add({Entity.Id, nullptr, Entity.ToJson()});
    TestTrue(TEXT("Write legacy entity"), Store.Commit(Edit)); Store.Close();
    FSQLiteDatabase Database;
    TestTrue(TEXT("Prepare schema 1 fixture"), Database.Open(*Path));
    TestTrue(TEXT("Remove v2 table"), Database.Execute(TEXT("DROP TABLE world_state")));
    TestTrue(TEXT("Set legacy schema"), Database.SetUserVersion(1)); Database.Close();
    TestTrue(TEXT("Migrate legacy schema"), Store.Open(Path));
    TestTrue(TEXT("Backup exists"), IFileManager::Get().FileExists(*(Path + TEXT(".before-v2.sqlite3"))));
    TMap<FString, FLCEntity> Entities; TMap<FString, TSharedPtr<FJsonObject>> Receipts; int64 Version;
    TSharedPtr<FJsonObject> Environment;
    TestTrue(TEXT("Read migrated entity"), Store.Load(Entities, Receipts, Version, &Environment));
    TestEqual(TEXT("Stable ID preserved"), Entities.Num(), 1);
    TestTrue(TEXT("Legacy ID exists"), Entities.Contains(TEXT("legacy-column")));
    TestFalse(TEXT("No invented old environment"), Environment.IsValid());
    TestTrue(TEXT("Old receipt preserved"), Receipts.Contains(TEXT("legacy-create"))); Store.Close();
    Database.Open(*Path); Database.SetUserVersion(3); Database.Close();
    TestFalse(TEXT("Future schema refused"), Store.Open(Path)); Store.Close();
    return true;
}
#endif
