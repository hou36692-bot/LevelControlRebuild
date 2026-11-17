#include "LCSemantic.h"
#include "LCStore.h"
#include "Misc/SecureHash.h"

static TSharedPtr<FJsonObject> QueryError(const FString& Status, const FString& Message, int64 Version)
{
    TSharedPtr<FJsonObject> Result = MakeShared<FJsonObject>(); Result->SetStringField(TEXT("status"), Status);
    Result->SetNumberField(TEXT("version"), Version);
    Result->SetArrayField(TEXT("warnings"), {MakeShared<FJsonValueString>(Message)});
    return Result;
}

static FString Text(const TSharedPtr<FJsonObject>& Value, const TCHAR* Key)
{ FString Result; Value->TryGetStringField(Key, Result); return Result; }

static TSharedPtr<FJsonObject> Canonical(const TSharedPtr<FJsonObject>& Value)
{
    TSharedPtr<FJsonObject> Result = MakeShared<FJsonObject>(); TArray<FString> Keys; for (const auto& Pair : Value->Values) Keys.Add(FString(Pair.Key)); Keys.Sort();
    for (const auto& Key : Keys)
    {
        auto Field = Value->TryGetField(Key);
        if (Field->Type == EJson::Object) Result->SetObjectField(Key, Canonical(Field->AsObject()));
        else Result->SetField(Key, Field);
    }
    return Result;
}

TSharedPtr<FJsonObject> FLCSemanticWorld::Query(const TSharedPtr<FJsonObject>& Request, FLCStore* Store)
{
    const double Started = FPlatformTime::Seconds();
    if (!Request) return QueryError(TEXT("rejected"), TEXT("Query must be an object"), CommittedVersion);
    const TSet<FString> Allowed = {TEXT("ids"), TEXT("name"), TEXT("role"), TEXT("source"), TEXT("area"), TEXT("pattern"), TEXT("ring"), TEXT("side"), TEXT("within"), TEXT("parent"), TEXT("bounds"), TEXT("near"), TEXT("relation"), TEXT("order"), TEXT("ordinal"), TEXT("limit"), TEXT("cursor"), TEXT("fields"), TEXT("mode"), TEXT("state")};
    for (const auto& Field : Request->Values) if (!Allowed.Contains(FString(Field.Key))) return QueryError(TEXT("rejected"), TEXT("Unknown query field: ") + FString(Field.Key), CommittedVersion);
    for (auto Key : {TEXT("within"), TEXT("mode"), TEXT("state"), TEXT("order"), TEXT("cursor")})
    {
        FString Value;
        if (Request->HasField(Key) && (!Request->TryGetStringField(Key, Value) || Value.IsEmpty() || Value.Len() > 256)) return QueryError(TEXT("rejected"), FString(Key) + TEXT(" must be a nonempty string"), CommittedVersion);
    }
    auto Options = Request;
    TArray<TSharedPtr<FJsonValue>> Matches;
    TSharedPtr<FJsonObject> SavedFrame;
    FString Cursor = Text(Request, TEXT("cursor"));
    int32 Offset = 0; double Limit = 32;
    TArray<FString> Ambiguities;
    if (!Cursor.IsEmpty())
    {
        const auto* Page = Pages.Find(Cursor);
        if (!Page || (*Page)->GetNumberField(TEXT("version")) != CommittedVersion || (*Page)->GetNumberField(TEXT("registry")) != RegistryRevision || (*Page)->GetNumberField(TEXT("expires")) < Started ||
            (Text((*Page)->GetObjectField(TEXT("options")), TEXT("state")) == TEXT("live") && (*Page)->GetNumberField(TEXT("sequence")) != Sequence))
            return QueryError(TEXT("resync_required"), TEXT("Query cursor expired; repeat the original query"), CommittedVersion);
        Options = (*Page)->GetObjectField(TEXT("options")); Matches = (*Page)->GetArrayField(TEXT("rows")); Offset = int32((*Page)->GetNumberField(TEXT("offset")));
        (*Page)->TryGetStringArrayField(TEXT("ambiguities"), Ambiguities);
        const TSharedPtr<FJsonObject>* FrameValue = nullptr; if ((*Page)->TryGetObjectField(TEXT("frame"), FrameValue)) SavedFrame = *FrameValue;
    }
    if (Options->HasField(TEXT("limit")) && (!Options->TryGetNumberField(TEXT("limit"), Limit) || !FMath::IsFinite(Limit) || Limit != FMath::FloorToDouble(Limit) || Limit < 1 || Limit > 256))
        return QueryError(TEXT("rejected"), TEXT("limit must be 1..256"), CommittedVersion);
    FString Mode = Text(Options, TEXT("mode")); if (Mode.IsEmpty()) Mode = TEXT("list");
    FString State = Text(Options, TEXT("state")); if (State.IsEmpty()) State = TEXT("committed");
    FString Order = Text(Options, TEXT("order")); if (Order.IsEmpty()) Order = TEXT("id");
    if ((Mode != TEXT("list") && Mode != TEXT("count") && Mode != TEXT("summary")) || (State != TEXT("committed") && State != TEXT("live")) || (Order != TEXT("id") && Order != TEXT("view_left") && Order != TEXT("distance")))
        return QueryError(TEXT("rejected"), TEXT("Invalid mode, state or order"), CommittedVersion);
    TArray<FString> RequestedFields;
    if (Options->HasField(TEXT("fields")) && !Options->TryGetStringArrayField(TEXT("fields"), RequestedFields)) return QueryError(TEXT("rejected"), TEXT("fields must be strings"), CommittedVersion);
    const TSet<FString> Fields = {TEXT("id"), TEXT("name"), TEXT("aliases"), TEXT("role"), TEXT("parent"), TEXT("source"), TEXT("area"), TEXT("pattern"), TEXT("ring"), TEXT("side"), TEXT("member_key"), TEXT("provenance"), TEXT("identity_scope"), TEXT("identity_conflict"), TEXT("relations"), TEXT("local_frame"), TEXT("capabilities"), TEXT("transform"), TEXT("bounds"), TEXT("semantic"), TEXT("tags"), TEXT("group"), TEXT("version"), TEXT("template"), TEXT("surface"), TEXT("collision"), TEXT("lod"), TEXT("live")};
    for (const auto& Field : RequestedFields) if (!Fields.Contains(Field)) return QueryError(TEXT("rejected"), TEXT("Unknown projected field: ") + Field, CommittedVersion);
    if (Cursor.IsEmpty())
    {
        TSet<FString> Candidates; bool Filtered = false;
        auto Intersect = [&](const TSet<FString>& Values)
        { if (!Filtered) { Candidates = Values; Filtered = true; } else { for (auto It = Candidates.CreateIterator(); It; ++It) if (!Values.Contains(*It)) It.RemoveCurrent(); } };
        for (auto Key : {TEXT("name"), TEXT("role"), TEXT("source"), TEXT("area"), TEXT("pattern"), TEXT("ring"), TEXT("side"), TEXT("parent")})
        {
            if (!Options->HasField(Key)) continue;
            FString Value; if (!Options->TryGetStringField(Key, Value) || Value.IsEmpty()) return QueryError(TEXT("rejected"), FString(Key) + TEXT(" must be a nonempty string"), CommittedVersion);
            Intersect(Index.FindRef(FString(Key) + TEXT("=") + Value));
        }
        TArray<FString> Explicit;
        if (Options->HasField(TEXT("ids")))
        {
            if (!Options->TryGetStringArrayField(TEXT("ids"), Explicit) || Explicit.Num() > 256) return QueryError(TEXT("rejected"), TEXT("ids requires at most 256 strings"), CommittedVersion);
            TSet<FString> Values;
            for (const auto& Id : Explicit) { if (!Rows.Contains(Id) || Values.Contains(Id)) return QueryError(TEXT("rejected"), TEXT("ids contains an unknown or duplicate identity"), CommittedVersion); Values.Add(Id); }
            Intersect(Values);
        }
        if (!Filtered) for (const auto& Pair : Rows) Candidates.Add(FString(Pair.Key));
        FString Within = Text(Options, TEXT("within"));
        if (!Within.IsEmpty() && !Rows.Contains(Within))
        {
            const auto Named = Index.FindRef(TEXT("name=") + Within);
            if (Named.Num() != 1) return QueryError(TEXT("ambiguous"), TEXT("within must resolve to exactly one identity; query its name first"), CommittedVersion);
            Within = Named.Array()[0];
        }
        FVector Min, Max, NearPoint; double Radius = 0; bool Spatial = false, HasNear = false;
        const TSharedPtr<FJsonObject>* Box = nullptr;
        if (Options->HasField(TEXT("bounds")))
        {
            if (!Options->TryGetObjectField(TEXT("bounds"), Box) || !LCReadVector(*Box, TEXT("min_cm"), Min) || !LCReadVector(*Box, TEXT("max_cm"), Max) || Min.X > Max.X || Min.Y > Max.Y || Min.Z > Max.Z)
                return QueryError(TEXT("rejected"), TEXT("Invalid query bounds"), CommittedVersion);
            Spatial = true;
        }
        const TSharedPtr<FJsonObject>* Near = nullptr;
        if (Options->HasField(TEXT("near")))
        {
            if (!Options->TryGetObjectField(TEXT("near"), Near) || !LCReadVector(*Near, TEXT("point_cm"), NearPoint) || !(*Near)->TryGetNumberField(TEXT("radius_cm"), Radius) || !FMath::IsFinite(Radius) || Radius < 0)
                return QueryError(TEXT("rejected"), TEXT("near needs point_cm and nonnegative radius_cm"), CommittedVersion);
            HasNear = true;
        }
        if (Order == TEXT("distance") && !HasNear) return QueryError(TEXT("rejected"), TEXT("distance order requires near"), CommittedVersion);
        if (Spatial && Store && State == TEXT("committed"))
        {
            TSet<FString> Broad;
            if (!Store->SpatialCandidates(Min, Max, Broad)) return QueryError(TEXT("failed"), Store->Error, CommittedVersion);
            for (auto It = Candidates.CreateIterator(); It; ++It) if (Physical.Contains(*It) && !Broad.Contains(*It)) It.RemoveCurrent();
        }
        const TSharedPtr<FJsonObject>* Relation = nullptr;
        FString RelationKind, Target;
        if (Options->HasField(TEXT("relation")) && (!Options->TryGetObjectField(TEXT("relation"), Relation) || !(*Relation)->TryGetStringField(TEXT("kind"), RelationKind) || !(*Relation)->TryGetStringField(TEXT("target"), Target)))
            return QueryError(TEXT("rejected"), TEXT("relation requires kind and target"), CommittedVersion);
        for (const auto& Id : Candidates)
        {
            if (!Within.IsEmpty() && !Descendant(Id, Within)) continue;
            auto Row = Rows[Id];
            if (Relation)
            {
                bool Found = false; const TArray<TSharedPtr<FJsonValue>>* Relations;
                if (Row->TryGetArrayField(TEXT("relations"), Relations)) for (const auto& R : *Relations) if (Text(R->AsObject(), TEXT("kind")) == RelationKind && Text(R->AsObject(), TEXT("target")) == Target) Found = true;
                if (!Found) continue;
            }
            if (State == TEXT("live"))
            {
                if (!Physical.Contains(Id) && !Bindings.Contains(Id))
                {
                    TArray<FString> Leaves; FString Error;
                    if (Expand(Id, Leaves, Error))
                    {
                        FBox Aggregate(ForceInit);
                        for (const auto& Leaf : Leaves) if (Live.Contains(Leaf))
                        {
                            const TSharedPtr<FJsonObject>* Bounds; FVector A, B;
                            if (Live[Leaf]->TryGetObjectField(TEXT("bounds"), Bounds) && LCReadVector(*Bounds, TEXT("min_cm"), A) && LCReadVector(*Bounds, TEXT("max_cm"), B)) Aggregate += FBox(A, B);
                        }
                        if (Aggregate.IsValid)
                        {
                            auto Transform = MakeShared<FJsonObject>(); Transform->SetArrayField(TEXT("position_cm"), LCVector(Aggregate.GetCenter())); Transform->SetArrayField(TEXT("size_cm"), LCVector(Aggregate.GetSize()));
                            Observe(Id, Transform, TEXT("aggregate"));
                        }
                    }
                }
                if (Bindings.Contains(Id)) Sample(Id);
                if (Live.Contains(Id))
                {
                    Row = LCParseJson(LCJsonString(Row)); Row->SetObjectField(TEXT("live"), Live[Id]);
                    Row->SetObjectField(TEXT("transform"), Live[Id]->GetObjectField(TEXT("transform")));
                    if (Live[Id]->HasField(TEXT("bounds"))) Row->SetObjectField(TEXT("bounds"), Live[Id]->GetObjectField(TEXT("bounds")));
                }
            }
            if (Spatial || HasNear)
            {
                const TSharedPtr<FJsonObject>* Geometry = nullptr; FVector A, B;
                if (!Row->TryGetObjectField(TEXT("bounds"), Geometry) || !LCReadVector(*Geometry, TEXT("min_cm"), A) || !LCReadVector(*Geometry, TEXT("max_cm"), B)) continue;
                const FBox Bounds(A, B);
                if (Spatial && !Bounds.Intersect(FBox(Min, Max))) continue;
                if (HasNear && Bounds.ComputeSquaredDistanceToPoint(NearPoint) > Radius * Radius) continue;
            }
            Matches.Add(MakeShared<FJsonValueObject>(Row));
        }
        TMap<FString, double> Positions;
        if (Order == TEXT("view_left"))
        {
            SavedFrame = Frame(); FVector Position, Forward, Right;
            if (!LCReadVector(SavedFrame, TEXT("position_cm"), Position) || !LCReadVector(SavedFrame, TEXT("forward"), Forward) || !LCReadVector(SavedFrame, TEXT("right"), Right))
                return QueryError(TEXT("ambiguous"), TEXT("Player camera is unavailable"), CommittedVersion);
            TSet<FString> Sources, Sides, Patterns, Areas;
            for (const auto& Value : Matches)
            {
                const auto Row = Value->AsObject(); const FString Id = Text(Row, TEXT("id"));
                Sources.Add(Text(Row, TEXT("source"))); Sides.Add(Text(Row, TEXT("side"))); Patterns.Add(Text(Row, TEXT("pattern")));
                Areas.Add(Text(Row, TEXT("area")));
                const TSharedPtr<FJsonObject>* Geometry = nullptr; FVector Point;
                if (!Row->TryGetObjectField(TEXT("transform"), Geometry) || !LCReadVector(*Geometry, TEXT("position_cm"), Point)) { Ambiguities.AddUnique(TEXT("Missing geometry")); continue; }
                const FVector Delta = Point - Position; const double Depth = FVector::DotProduct(Delta, Forward);
                if (Depth <= 1) { Ambiguities.AddUnique(TEXT("Candidates behind or on the camera plane; specify a side")); continue; }
                Positions.Add(Id, FVector::DotProduct(Delta, Right) / Depth);
                if (Text(Row, TEXT("provenance")) == TEXT("inferred")) Ambiguities.AddUnique(TEXT("Inferred semantics require annotation before ordinal editing"));
            }
            if (Sources.Num() > 1 || Areas.Num() > 1 || Sides.Num() > 1 || (Patterns.Num() > 1 && !Options->HasField(TEXT("side")))) Ambiguities.AddUnique(TEXT("Multiple structures/areas/sides/patterns; narrow the query"));
        }
        if (Order == TEXT("distance")) for (const auto& Value : Matches)
        {
            FVector Point; const TSharedPtr<FJsonObject>* Geometry = nullptr;
            if (Value->AsObject()->TryGetObjectField(TEXT("transform"), Geometry) && LCReadVector(*Geometry, TEXT("position_cm"), Point)) Positions.Add(Text(Value->AsObject(), TEXT("id")), FVector::DistSquared(Point, NearPoint));
        }
        Matches.Sort([&](const TSharedPtr<FJsonValue>& A, const TSharedPtr<FJsonValue>& B)
        { const FString IA = Text(A->AsObject(), TEXT("id")), IB = Text(B->AsObject(), TEXT("id")); const double PA = Positions.FindRef(IA), PB = Positions.FindRef(IB); return PA == PB ? IA < IB : PA < PB; });
        if (Order != TEXT("id")) for (int32 I = 1; I < Matches.Num(); ++I)
            if (FMath::Abs(Positions.FindRef(Text(Matches[I]->AsObject(), TEXT("id"))) - Positions.FindRef(Text(Matches[I-1]->AsObject(), TEXT("id")))) < 1e-6)
                Ambiguities.AddUnique(TEXT("Projected/distance ordering is tied; narrow the query"));
    }
    TSharedPtr<FJsonObject> Result = MakeShared<FJsonObject>();
    Result->SetStringField(TEXT("status"), Ambiguities.IsEmpty() ? TEXT("ok") : TEXT("ambiguous"));
    Result->SetStringField(TEXT("session_id"), Session); Result->SetNumberField(TEXT("version"), CommittedVersion);
    Result->SetNumberField(TEXT("registry_revision"), RegistryRevision); Result->SetNumberField(TEXT("state_sequence"), Sequence);
    Result->SetStringField(TEXT("state"), State); Result->SetNumberField(TEXT("count"), Matches.Num());
    Result->SetObjectField(TEXT("matched_by"), Canonical(Options)); if (SavedFrame) Result->SetObjectField(TEXT("frame"), SavedFrame);
    TArray<TSharedPtr<FJsonValue>> Warnings; for (const auto& Message : Ambiguities) Warnings.Add(MakeShared<FJsonValueString>(Message));
    if (Matches.IsEmpty()) Warnings.Add(MakeShared<FJsonValueString>(TEXT("No match; missing metadata is not inferred from shape")));
    Result->SetArrayField(TEXT("warnings"), Warnings);
    if (Mode == TEXT("summary"))
    {
        int32 Unclassified = 0, ReadOnly = 0, Persistent = 0, Conflicts = 0; TSharedPtr<FJsonObject> Roles = MakeShared<FJsonObject>();
        for (const auto& Value : Matches)
        {
            auto Row = Value->AsObject(); FString Role = Text(Row, TEXT("role")); if (Role.IsEmpty()) Role = TEXT("unclassified");
            double Count = 0; Roles->TryGetNumberField(Role, Count); Roles->SetNumberField(Role, Count + 1);
            if (Role == TEXT("unclassified")) ++Unclassified;
            if (!Physical.Contains(Text(Row, TEXT("id"))) && Bindings.Contains(Text(Row, TEXT("id")))) ++ReadOnly;
            if (Text(Row, TEXT("identity_scope")) == TEXT("persistent")) ++Persistent;
            bool Conflict = false; Row->TryGetBoolField(TEXT("identity_conflict"), Conflict); if (Conflict) ++Conflicts;
        }
        Result->SetNumberField(TEXT("registered"), Rows.Num()); Result->SetNumberField(TEXT("unclassified"), Unclassified);
        Result->SetNumberField(TEXT("read_only_geometry"), ReadOnly); Result->SetNumberField(TEXT("persistent"), Persistent); Result->SetNumberField(TEXT("identity_conflicts"), Conflicts);
        Result->SetObjectField(TEXT("roles"), Canonical(Roles)); Result->SetStringField(TEXT("coverage"), TEXT("registered world primitives and explicit identities; internal/preview objects excluded"));
    }
    if (Mode == TEXT("list"))
    {
        double Ordinal = 0;
        if (Options->HasField(TEXT("ordinal")))
        {
            if (!Options->TryGetNumberField(TEXT("ordinal"), Ordinal) || !FMath::IsFinite(Ordinal) || Ordinal < 1 || Ordinal != FMath::FloorToDouble(Ordinal)) return QueryError(TEXT("rejected"), TEXT("ordinal must be a positive integer"), CommittedVersion);
            if (Order == TEXT("id")) return QueryError(TEXT("rejected"), TEXT("ordinal requires explicit spatial ordering"), CommittedVersion);
            if (Ambiguities.IsEmpty())
            {
                Offset = int32(FMath::Min(Ordinal - 1, double(Matches.Num()))); Limit = 1;
                if (Ordinal > Matches.Num())
                {
                    Warnings.Add(MakeShared<FJsonValueString>(FString::Printf(
                        TEXT("ordinal %.0f exceeds %d matches"), Ordinal, Matches.Num())));
                    Result->SetArrayField(TEXT("warnings"), Warnings);
                }
            }
        }
        TArray<TSharedPtr<FJsonValue>> Values; TArray<FString> Selected;
        for (int32 I = Offset; I < FMath::Min(Offset + int32(Limit), Matches.Num()); ++I)
        {
            auto Row = Matches[I]->AsObject(); TSharedPtr<FJsonObject> Projection = MakeShared<FJsonObject>();
            const TArray<FString> Default = {TEXT("id"), TEXT("name"), TEXT("role"), TEXT("parent"), TEXT("source"), TEXT("capabilities"), TEXT("identity_scope"), TEXT("provenance")};
            for (const auto& Field : RequestedFields.IsEmpty() ? Default : RequestedFields) if (Row->HasField(Field)) Projection->SetField(Field, Row->TryGetField(Field));
            const FString Id = Text(Row, TEXT("id")); Projection->SetStringField(TEXT("id"), Id); Selected.Add(Id);
            Values.Add(MakeShared<FJsonValueObject>(Canonical(Projection)));
        }
        Result->SetArrayField(TEXT("entities"), Values);
        if (Ambiguities.IsEmpty() && !Selected.IsEmpty())
        {
            FLCSemanticSelection Selection; Selection.Version = CommittedVersion; Selection.Expires = Started + 120;
            bool Editable = true; FString Error;
            for (const auto& Id : Selected) if (!Expand(Id, Selection.Ids, Error)) Editable = false;
            if (Editable)
            {
                for (const auto& Id : Selection.Ids) Selection.Fingerprints.Add(Id, Live.Contains(Id) ? LCJsonString(Live[Id]->GetObjectField(TEXT("transform"))) : TEXT(""));
                for (auto It = Selections.CreateIterator(); It; ++It) if (It.Value().Expires < Started) It.RemoveCurrent();
                if (Selections.Num() >= 256) Selections.Reset();
                const FString Token = Session + TEXT("-") + FGuid::NewGuid().ToString(EGuidFormats::Digits);
                Selections.Add(Token, Selection); Result->SetStringField(TEXT("selection_id"), Token);
                Result->SetNumberField(TEXT("expected_version"), CommittedVersion);
            }
        }
        if (!Ordinal && Offset + int32(Limit) < Matches.Num())
        {
            TSharedPtr<FJsonObject> Page = MakeShared<FJsonObject>(); Page->SetArrayField(TEXT("rows"), Matches); Page->SetObjectField(TEXT("options"), Options);
            Page->SetNumberField(TEXT("offset"), Offset + int32(Limit)); Page->SetNumberField(TEXT("version"), CommittedVersion); Page->SetNumberField(TEXT("registry"), RegistryRevision);
            Page->SetNumberField(TEXT("sequence"), Sequence); Page->SetNumberField(TEXT("expires"), Started + 120); if (SavedFrame) Page->SetObjectField(TEXT("frame"), SavedFrame);
            TArray<TSharedPtr<FJsonValue>> Issues; for (const auto& Issue : Ambiguities) Issues.Add(MakeShared<FJsonValueString>(Issue)); Page->SetArrayField(TEXT("ambiguities"), Issues);
            if (Pages.Num() >= 16) Pages.Reset();
            const FString Token = FGuid::NewGuid().ToString(EGuidFormats::Digits); Pages.Add(Token, Page); Result->SetStringField(TEXT("next_cursor"), Token);
        }
    }
    auto Content = Canonical(Result); Content->RemoveField(TEXT("selection_id")); Content->RemoveField(TEXT("frame")); Content->RemoveField(TEXT("state_sequence")); Content->RemoveField(TEXT("next_cursor"));
    Result->SetStringField(TEXT("content_hash"), FMD5::HashAnsiString(*LCJsonString(Content)));
    Result->SetNumberField(TEXT("ms"), (FPlatformTime::Seconds() - Started) * 1000);
    return Result;
}

TSharedPtr<FJsonObject> FLCSemanticWorld::Changes(const TSharedPtr<FJsonObject>& Request, FLCStore& Store)
{
    if (!Request) return QueryError(TEXT("rejected"), TEXT("changes requires an object"), CommittedVersion);
    int64 Since = CommittedVersion, LiveSince = Sequence;
    const TSharedPtr<FJsonObject>* Cursor = nullptr;
    if (Request->HasField(TEXT("cursor")))
    {
        if (!Request->TryGetObjectField(TEXT("cursor"), Cursor) || Text(*Cursor, TEXT("session_id")) != Session || !(*Cursor)->TryGetNumberField(TEXT("version"), Since) || !(*Cursor)->TryGetNumberField(TEXT("sequence"), LiveSince) || Since < 0 || Since > CommittedVersion || LiveSince < 0 || LiveSince > Sequence)
            return QueryError(TEXT("resync_required"), TEXT("Invalid cursor or game restarted"), CommittedVersion);
        if (!LiveEvents.IsEmpty() && LiveSince < LiveEvents[0]->AsObject()->GetNumberField(TEXT("sequence")) - 1)
            return QueryError(TEXT("resync_required"), TEXT("Live history expired"), CommittedVersion);
    }
    auto Result = Store.Changes(Since, CommittedVersion);
    if (Result->HasField(TEXT("error"))) return QueryError(TEXT("failed"), Result->GetStringField(TEXT("error")), CommittedVersion);
    TSharedPtr<FJsonObject> Next = MakeShared<FJsonObject>(); Next->SetStringField(TEXT("session_id"), Session);
    Next->SetNumberField(TEXT("version"), Result->GetNumberField(TEXT("through_version")));
    TArray<TSharedPtr<FJsonValue>> Values;
    int64 Through = LiveSince;
    for (const auto& Value : LiveEvents) if (Value->AsObject()->GetNumberField(TEXT("sequence")) > LiveSince)
    { if (Values.Num() >= 256) break; Values.Add(Value); Through = int64(Value->AsObject()->GetNumberField(TEXT("sequence"))); }
    Next->SetNumberField(TEXT("sequence"), Through); Result->SetObjectField(TEXT("cursor"), Next);
    Result->SetArrayField(TEXT("live"), Values); Result->SetStringField(TEXT("status"), TEXT("ok"));
    return Result;
}
