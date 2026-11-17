#include "LCSelector.h"

bool LCResolve(const FString& Selector, const TMap<FString, FLCEntity>& Entities,
    const FVector& Origin, bool Nearest, bool AllowEmpty, TArray<FString>& Ids, FString& Error)
{
    if (Selector.IsEmpty() || Selector.Len() > 4096) { Error = TEXT("Invalid selector"); return false; }
    TArray<FString> Patterns;
    Selector.ParseIntoArray(Patterns, TEXT(","), false);
    TSet<FString> Selected;
    for (const auto& Term : Patterns)
    {
        const bool Tag = Term.StartsWith(TEXT("tag:"));
        const bool Group = Term.StartsWith(TEXT("group:"));
        const FString Pattern = Tag ? Term.Mid(4) : Group ? Term.Mid(6) : Term;
        if (Pattern.IsEmpty()) { Error = TEXT("Empty selector term"); return false; }
        for (TCHAR Char : Pattern)
            if (!((Char >= 'a' && Char <= 'z') || (Char >= '0' && Char <= '9') || Char == '-' || Char == '*'))
            { Error = TEXT("Selector supports lowercase IDs, '*' and commas"); return false; }
        bool Matched = false;
        for (const auto& Item : Entities)
        {
            bool Match = Group ? Item.Value.Group.MatchesWildcard(Pattern, ESearchCase::CaseSensitive) :
                !Tag && Item.Key.MatchesWildcard(Pattern, ESearchCase::CaseSensitive);
            if (Tag) for (const auto& Value : Item.Value.Tags) if (Value.MatchesWildcard(Pattern, ESearchCase::CaseSensitive)) Match = true;
            if (Match) { Selected.Add(Item.Key); Matched = true; }
        }
        if (!Matched && (!AllowEmpty || !Pattern.Contains(TEXT("*")))) { Error = TEXT("Selector matched no entities: ") + Pattern; return false; }
    }
    Ids = Selected.Array();
    Ids.Sort();
    if (Nearest && Ids.Num() > 0)
    {
        FString Best = Ids[0];
        double Distance = FVector::DistSquared(Entities[Best].Position, Origin);
        for (const auto& Id : Ids)
        {
            const double Candidate = FVector::DistSquared(Entities[Id].Position, Origin);
            if (Candidate < Distance) { Best = Id; Distance = Candidate; }
        }
        Ids = {Best};
    }
    return true;
}
