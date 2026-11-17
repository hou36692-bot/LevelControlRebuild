#include "LCSurface.h"
#include "LCTypes.h"
#include "Components/InstancedStaticMeshComponent.h"
#include "Components/DecalComponent.h"
#include "Engine/StaticMesh.h"
#include "GameFramework/Actor.h"
#include "Materials/MaterialInstanceDynamic.h"

static TSharedPtr<FJsonObject> LCSurfaceDefinitions()
{
    static TSharedPtr<FJsonObject> Presets;
    if (!Presets)
    {
        Presets = MakeShared<FJsonObject>();
        struct FSurface { const TCHAR* Id; const TCHAR* Category; FVector Color; double Roughness; double Metallic; double Emissive; };
        const FSurface Definitions[] = {
            {TEXT("stone-limestone"), TEXT("stone"), FVector(0.65,0.58,0.43),0.8,0,0},
            {TEXT("stone-slate"), TEXT("stone"), FVector(0.12,0.17,0.2),0.7,0,0},
            {TEXT("stone-brick"), TEXT("stone"), FVector(0.45,0.12,0.07),0.9,0,0},
            {TEXT("wood-oak"), TEXT("wood"), FVector(0.45,0.22,0.07),0.65,0,0},
            {TEXT("wood-walnut"), TEXT("wood"), FVector(0.12,0.045,0.018),0.5,0,0},
            {TEXT("wood-painted"), TEXT("wood"), FVector(0.1,0.3,0.32),0.55,0,0},
            {TEXT("metal-steel"), TEXT("metal"), FVector(0.55,0.6,0.65),0.25,1,0},
            {TEXT("metal-brass"), TEXT("metal"), FVector(0.8,0.52,0.12),0.3,1,0},
            {TEXT("metal-rust"), TEXT("metal"), FVector(0.32,0.1,0.035),0.85,0.55,0},
            {TEXT("cloth-linen"), TEXT("cloth"), FVector(0.75,0.66,0.5),0.95,0,0},
            {TEXT("cloth-red"), TEXT("cloth"), FVector(0.45,0.025,0.04),0.9,0,0},
            {TEXT("cloth-blue"), TEXT("cloth"), FVector(0.02,0.09,0.35),0.9,0,0},
            {TEXT("glass-clear"), TEXT("glass"), FVector(0.8,0.95,1),0.06,0,0},
            {TEXT("glass-amber"), TEXT("glass"), FVector(1,0.42,0.07),0.12,0,0},
            {TEXT("glass-frosted"), TEXT("glass"), FVector(0.7,0.82,0.9),0.4,0,0},
            {TEXT("emissive-warm"), TEXT("emissive"), FVector(1,0.4,0.08),0.3,0,6},
            {TEXT("emissive-cool"), TEXT("emissive"), FVector(0.08,0.5,1),0.3,0,6},
            {TEXT("emissive-green"), TEXT("emissive"), FVector(0.06,1,0.25),0.3,0,6}
        };
        for (const auto& Definition : Definitions)
        {
            auto Surface = MakeShared<FJsonObject>();
            Surface->SetStringField(TEXT("preset"), Definition.Id);
            Surface->SetStringField(TEXT("category"), Definition.Category);
            Surface->SetArrayField(TEXT("tint"), LCVector(Definition.Color));
            Surface->SetNumberField(TEXT("roughness"), Definition.Roughness);
            Surface->SetNumberField(TEXT("metallic"), Definition.Metallic);
            Surface->SetNumberField(TEXT("emissive"), Definition.Emissive);
            Surface->SetNumberField(TEXT("normal_strength"), 0.25);
            Surface->SetNumberField(TEXT("wetness"), 0);
            Presets->SetObjectField(Definition.Id, Surface);
        }
    }
    return Presets;
}

TSharedPtr<FJsonObject> LCSurfacePresets()
{
    return LCParseJson(LCJsonString(LCSurfaceDefinitions()));
}

bool LCSurfacePatch(const TSharedPtr<FJsonObject>& Current, const FString& Preset,
    const TSharedPtr<FJsonObject>& Values, TSharedPtr<FJsonObject>& Out, FString& Error)
{
    if (!Preset.IsEmpty())
    {
        const TSharedPtr<FJsonObject>* Selected = nullptr;
        // Clone only the selected preset, not the entire catalog for every entity parse.
        auto Presets = LCSurfaceDefinitions();
        if (!Presets->TryGetObjectField(Preset, Selected)) { Error = TEXT("Unknown surface preset: ") + Preset; return false; }
        Out = LCParseJson(LCJsonString(*Selected));
    }
    else Out = LCParseJson(LCJsonString(Current ? Current : LCSurfaceDefinitions()->GetObjectField(TEXT("stone-limestone"))));
    if (!Values) return true;
    for (const auto& Field : Values->Values)
    {
        const FString Key(Field.Key);
        if (Key == TEXT("preset") || Key == TEXT("category"))
        {
            if (Field.Value->Type != EJson::String || Field.Value->AsString() != Out->GetStringField(Key))
            { Error = TEXT("Surface identity does not match its preset"); return false; }
        }
        else if (Key == TEXT("tint"))
        {
            FVector Color;
            if (!LCReadVector(Values, Key, Color) || Color.GetMin() < 0 || Color.GetMax() > 1)
            { Error = TEXT("tint must be three numbers within 0..1"); return false; }
        }
        else
        {
            const double Maximum = Key == TEXT("emissive") ? 50 : Key == TEXT("normal_strength") ? 2 : 1;
            const TSet<FString> Scalars = {TEXT("roughness"), TEXT("metallic"), TEXT("emissive"), TEXT("normal_strength"), TEXT("wetness")};
            double Value;
            if (!Scalars.Contains(Key) || Field.Value->Type != EJson::Number || !Field.Value->TryGetNumber(Value) || !FMath::IsFinite(Value) || Value < 0 || Value > Maximum)
            { Error = TEXT("Unknown or out-of-range surface parameter: ") + Key; return false; }
        }
        Out->SetField(Key, Field.Value);
    }
    return true;
}

FString LCBatchKey(const FLCEntity& Entity)
{
    if (!Entity.Surface && Entity.Collision && !Entity.Lod) return Entity.Template;
    return Entity.Template + TEXT("|") + (Entity.Surface ? LCJsonString(Entity.Surface) : TEXT("default")) +
        (Entity.Collision ? TEXT("|collision|") : TEXT("|no-collision|")) + LexToString(Entity.Lod);
}

UInstancedStaticMeshComponent* LCCreateBatch(AActor* Host, const FLCEntity& Entity, bool Preview, FString& Error)
{
    const auto* Template = LCTemplates().FindByPredicate([&](const FLCTemplate& Item) { return Item.Id == Entity.Template; });
    auto* Mesh = Template ? LoadObject<UStaticMesh>(nullptr, *Template->Mesh) : nullptr;
    if (!Mesh) { Error = TEXT("Cooked mesh is unavailable: ") + Entity.Template; return nullptr; }
    if (Entity.Lod > Mesh->GetNumLODs()) { Error = TEXT("Requested LOD is not present in the cooked mesh"); return nullptr; }
    UMaterialInstanceDynamic* Material = nullptr;
    if (Entity.Surface)
    {
        const auto Category = Entity.Surface->GetStringField(TEXT("category"));
        const auto Path = Preview
            ? (Category == TEXT("glass") ? TEXT("/Game/LC2/Materials/M_Growth_Glass") : TEXT("/Game/LC2/Materials/M_Growth_Surface"))
            : (Category == TEXT("glass") ? TEXT("/Game/LC2/Materials/M_Surface_Glass") : TEXT("/Game/LC2/Materials/M_Surface"));
        auto* Parent = LoadObject<UMaterialInterface>(nullptr, Path);
        if (!Parent) { Error = TEXT("Cooked surface material is unavailable; generate and Cook LC2 content"); return nullptr; }
        Material = UMaterialInstanceDynamic::Create(Parent, Host);
        FVector Color; LCReadVector(Entity.Surface, TEXT("tint"), Color);
        Material->SetVectorParameterValue(TEXT("Tint"), FLinearColor(Color));
        const TArray<FString> Parameters = {TEXT("roughness"), TEXT("metallic"), TEXT("emissive"), TEXT("normal_strength"), TEXT("wetness")};
        for (const auto& Name : Parameters) Material->SetScalarParameterValue(FName(Name), Entity.Surface->GetNumberField(Name));
        Material->SetScalarParameterValue(TEXT("pattern"), Category == TEXT("wood") ? 1 : Category == TEXT("cloth") ? 2 : Category == TEXT("stone") ? 3 : 0);
    }
    if (Preview && !Material)
    {
        auto* Parent = LoadObject<UMaterialInterface>(nullptr, TEXT("/Game/LC2/Materials/M_Growth_Default"));
        if (!Parent) { Error = TEXT("Cooked growth material is unavailable"); return nullptr; }
        Material = UMaterialInstanceDynamic::Create(Parent, Host);
    }
    if (Preview) Material->SetScalarParameterValue(TEXT("LCRevealHeightCM"), -100000000);
    auto* Batch = NewObject<UInstancedStaticMeshComponent>(Host);
    Batch->SetStaticMesh(Mesh);
    if (Material) for (int32 Index = 0; Index < Mesh->GetStaticMaterials().Num(); ++Index) Batch->SetMaterial(Index, Material);
    Batch->SetMobility(EComponentMobility::Movable);
    Batch->SetCollisionProfileName(TEXT("BlockAll"));
    if (Preview || !Entity.Collision) Batch->SetCollisionEnabled(ECollisionEnabled::NoCollision);
    Batch->SetForcedLodModel(Entity.Lod);
    Batch->SetRemoveSwap();
    Batch->SetupAttachment(Host->GetRootComponent());
    Host->AddInstanceComponent(Batch); Batch->RegisterComponent();
    return Batch;
}

UDecalComponent* LCCreateDecal(AActor* Host, const FLCEntity& Entity, FString& Error, bool Preview)
{
    auto* Parent = LoadObject<UMaterialInterface>(nullptr, Preview ? TEXT("/Game/LC2/Materials/M_Growth_Decal") : TEXT("/Game/LC2/Materials/M_Decal"));
    if (!Parent) { Error = TEXT("Cooked decal material is unavailable"); return nullptr; }
    auto* Material = UMaterialInstanceDynamic::Create(Parent, Host);
    Material->SetScalarParameterValue(TEXT("pattern"), Entity.Template == TEXT("decal/stain") ? 0 : Entity.Template == TEXT("decal/marker") ? 1 : 2);
    FVector Color = Entity.Template == TEXT("decal/stain") ? FVector(0.045,0.02,0.005) : Entity.Template == TEXT("decal/marker") ? FVector(1,0.6,0.05) : FVector(0.05,0.7,1);
    if (Entity.Surface) LCReadVector(Entity.Surface, TEXT("tint"), Color);
    Material->SetVectorParameterValue(TEXT("Tint"), FLinearColor(Color));
    if (Preview) Material->SetScalarParameterValue(TEXT("LCRevealHeightCM"), -100000000);
    auto* Decal = NewObject<UDecalComponent>(Host);
    Decal->DecalSize = FVector(50);
    Decal->SetDecalMaterial(Material);
    Decal->SetupAttachment(Host->GetRootComponent());
    Host->AddInstanceComponent(Decal); Decal->RegisterComponent();
    Decal->SetWorldTransform(Entity.Transform());
    return Decal;
}
