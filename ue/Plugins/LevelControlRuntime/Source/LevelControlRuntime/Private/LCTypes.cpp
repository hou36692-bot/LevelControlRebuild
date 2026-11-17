#include "LCTypes.h"
#include "LCSemantic.h"
#include "LCSurface.h"
#include "Serialization/JsonSerializer.h"
#include "Interfaces/IPluginManager.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"

TSharedPtr<FJsonObject> LCAssetCatalog()
{
    static TSharedPtr<FJsonObject> Catalog;
    if (!Catalog)
    {
        const auto Plugin = IPluginManager::Get().FindPlugin(TEXT("LevelControlRuntime"));
        FString Text;
        if (Plugin && FFileHelper::LoadFileToString(Text, *(Plugin->GetBaseDir() / TEXT("Resources/catalog.json")))) Catalog = LCParseJson(Text);
        if (!Catalog)
        {
            Catalog = MakeShared<FJsonObject>();
            Catalog->SetStringField(TEXT("error"), TEXT("Cooked asset catalog is unavailable"));
            Catalog->SetArrayField(TEXT("props"), {}); Catalog->SetArrayField(TEXT("prefabs"), {});
        }
    }
    return Catalog;
}

const TArray<FLCTemplate>& LCTemplates()
{
    static const TArray<FLCTemplate> Templates = [] {
        TArray<FLCTemplate> Values = {
        {TEXT("blockout/box"), TEXT("/Engine/BasicShapes/Cube"), FVector(100)},
        {TEXT("blockout/column"), TEXT("/Engine/BasicShapes/Cylinder"), FVector(100, 100, 500)},
        {TEXT("blockout/wall"), TEXT("/Engine/BasicShapes/Cube"), FVector(20, 500, 300)},
        {TEXT("blockout/floor"), TEXT("/Engine/BasicShapes/Cube"), FVector(500, 500, 20)},
        {TEXT("decal/stain"), TEXT("/Engine/BasicShapes/Cube"), FVector(10, 200, 200)},
        {TEXT("decal/marker"), TEXT("/Engine/BasicShapes/Cube"), FVector(10, 100, 100)},
        {TEXT("decal/path"), TEXT("/Engine/BasicShapes/Cube"), FVector(10, 200, 200)}
        };
        for (const auto& Value : LCAssetCatalog()->GetArrayField(TEXT("props")))
        {
            auto Prop = Value->AsObject();
            FLCTemplate Template;
            if (!Prop->TryGetStringField(TEXT("id"), Template.Id) || !Template.Id.StartsWith(TEXT("prop/")) ||
                !Prop->TryGetStringField(TEXT("mesh"), Template.Mesh) || !Template.Mesh.StartsWith(TEXT("/Game/LC2/Props/")) ||
                !LCReadVector(Prop, TEXT("size_cm"), Template.Size) || Template.Size.GetMin() <= 0 ||
                !LCReadVector(Prop, TEXT("mesh_size_cm"), Template.MeshSize) || Template.MeshSize.GetMin() <= 0 ||
                !LCReadVector(Prop, TEXT("mesh_center_cm"), Template.MeshCenter)) continue;
            Values.Add(Template);
        }
        return Values;
    }();
    return Templates;
}

bool LCValidId(const FString& Id)
{
    if (Id.IsEmpty() || Id.Len() > 128 || Id[0] < 'a' || Id[0] > 'z' || Id.EndsWith(TEXT("-"))) return false;
    for (TCHAR Char : Id)
        if (!((Char >= 'a' && Char <= 'z') || (Char >= '0' && Char <= '9') || Char == '-')) return false;
    return !Id.Contains(TEXT("--"));
}

TArray<TSharedPtr<FJsonValue>> LCVector(const FVector& Value)
{
    return {MakeShared<FJsonValueNumber>(Value.X), MakeShared<FJsonValueNumber>(Value.Y), MakeShared<FJsonValueNumber>(Value.Z)};
}

bool LCReadVector(const TSharedPtr<FJsonObject>& Json, const FString& Key, FVector& Out)
{
    const TArray<TSharedPtr<FJsonValue>>* Values = nullptr;
    if (!Json || !Json->TryGetArrayField(Key, Values) || Values->Num() != 3) return false;
    for (int32 Index = 0; Index < 3; ++Index)
    {
        double Number;
        if (!(*Values)[Index]->TryGetNumber(Number) || !FMath::IsFinite(Number) || FMath::Abs(Number) > 1e8) return false;
        Out[Index] = Number;
    }
    return true;
}

FString LCJsonString(const TSharedPtr<FJsonObject>& Json)
{
    FString Text;
    FJsonSerializer::Serialize(Json.ToSharedRef(), TJsonWriterFactory<TCHAR, TCondensedJsonPrintPolicy<TCHAR>>::Create(&Text));
    return Text;
}

TSharedPtr<FJsonObject> LCParseJson(const FString& Text)
{
    TSharedPtr<FJsonObject> Json;
    FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Text), Json);
    return Json;
}

FTransform FLCEntity::Transform() const
{
    const auto* Asset = LCTemplates().FindByPredicate([&](const FLCTemplate& Value) { return Value.Id == Template; });
    const FVector Scale = Size / (Asset ? Asset->MeshSize : FVector(100));
    const FRotator Orientation(Rotation.X, Rotation.Y, Rotation.Z);
    return FTransform(Orientation, Position - Orientation.RotateVector((Asset ? Asset->MeshCenter : FVector::ZeroVector) * Scale), Scale);
}

FBox FLCEntity::Bounds() const
{
    return FBox(-Size / 2, Size / 2).TransformBy(FTransform(FRotator(Rotation.X, Rotation.Y, Rotation.Z), Position));
}

TSharedPtr<FJsonObject> FLCEntity::ToJson() const
{
    auto Json = MakeShared<FJsonObject>();
    Json->SetStringField(TEXT("id"), Id);
    Json->SetStringField(TEXT("template"), Template);
    auto Geometry = MakeShared<FJsonObject>();
    Geometry->SetArrayField(TEXT("position_cm"), LCVector(Position));
    Geometry->SetArrayField(TEXT("rotation_deg"), LCVector(Rotation));
    Geometry->SetArrayField(TEXT("size_cm"), LCVector(Size));
    Json->SetObjectField(TEXT("transform"), Geometry);
    auto Box = MakeShared<FJsonObject>();
    Box->SetArrayField(TEXT("min_cm"), LCVector(Bounds().Min));
    Box->SetArrayField(TEXT("max_cm"), LCVector(Bounds().Max));
    Json->SetObjectField(TEXT("bounds"), Box);
    TArray<TSharedPtr<FJsonValue>> TagValues;
    for (const auto& Tag : Tags) TagValues.Add(MakeShared<FJsonValueString>(Tag));
    Json->SetArrayField(TEXT("tags"), TagValues);
    Json->SetStringField(TEXT("group"), Group);
    Json->SetStringField(TEXT("source"), Source);
    Json->SetNumberField(TEXT("version"), Version);
    if (Surface) Json->SetObjectField(TEXT("surface"), Surface);
    if (!Collision) Json->SetBoolField(TEXT("collision"), false);
    if (Lod) Json->SetNumberField(TEXT("lod"), Lod);
    if (Semantic) Json->SetObjectField(TEXT("semantic"), Semantic);
    return Json;
}

bool FLCEntity::FromJson(const TSharedPtr<FJsonObject>& Json, FLCEntity& Entity, FString& Error)
{
    const TSharedPtr<FJsonObject>* Geometry = nullptr;
    if (!Json || !Json->TryGetStringField(TEXT("id"), Entity.Id) || !LCValidId(Entity.Id)) Error = TEXT("Invalid kebab-case entity id");
    else if (!Json->TryGetStringField(TEXT("template"), Entity.Template) ||
        !LCTemplates().ContainsByPredicate([&](const FLCTemplate& Item) { return Item.Id == Entity.Template; })) Error = TEXT("Unknown template");
    else if (!Json->TryGetObjectField(TEXT("transform"), Geometry) ||
        !LCReadVector(*Geometry, TEXT("position_cm"), Entity.Position) ||
        !LCReadVector(*Geometry, TEXT("size_cm"), Entity.Size) || Entity.Size.GetMin() <= 0 || Entity.Size.GetMax() > 1e6) Error = TEXT("Invalid position or size");
    else if ((*Geometry)->HasField(TEXT("rotation_deg")) && !LCReadVector(*Geometry, TEXT("rotation_deg"), Entity.Rotation)) Error = TEXT("Invalid rotation");
    else
    {
        const TSharedPtr<FJsonObject>* Semantic = nullptr;
        if (Json->HasField(TEXT("semantic")))
        {
            if (!Json->TryGetObjectField(TEXT("semantic"), Semantic) || !LCValidateSemantic(*Semantic, Error)) return false;
            Entity.Semantic = LCParseJson(LCJsonString(*Semantic));
        }
        const TSharedPtr<FJsonObject>* Surface = nullptr;
        if (Json->HasField(TEXT("surface")))
        {
            FString Preset;
            if (!Json->TryGetObjectField(TEXT("surface"), Surface) || !(*Surface)->TryGetStringField(TEXT("preset"), Preset) ||
                !LCSurfacePatch(nullptr, Preset, *Surface, Entity.Surface, Error)) return false;
        }
        if (Json->HasField(TEXT("collision")) && !Json->TryGetBoolField(TEXT("collision"), Entity.Collision))
        { Error = TEXT("collision must be boolean"); return false; }
        double Lod = 0;
        if (Json->HasField(TEXT("lod")) && (!Json->TryGetNumberField(TEXT("lod"), Lod) || !FMath::IsFinite(Lod) || Lod != FMath::FloorToDouble(Lod) || Lod < 0 || Lod > 4))
        { Error = TEXT("lod must be an integer within 0..4"); return false; }
        Entity.Lod = int32(Lod);
        if (Entity.Template.StartsWith(TEXT("decal/"))) Entity.Collision = false;
        if (Json->HasField(TEXT("tags")) && !Json->TryGetStringArrayField(TEXT("tags"), Entity.Tags)) { Error = TEXT("tags must be strings"); return false; }
        if (Json->HasField(TEXT("group")) && !Json->TryGetStringField(TEXT("group"), Entity.Group)) { Error = TEXT("group must be a string"); return false; }
        if (Json->HasField(TEXT("source")) && (!Json->TryGetStringField(TEXT("source"), Entity.Source) ||
            (!Entity.Source.IsEmpty() && !LCValidId(Entity.Source)))) { Error = TEXT("Invalid entity source"); return false; }
        Json->TryGetNumberField(TEXT("version"), Entity.Version);
        return true;
    }
    return false;
}
