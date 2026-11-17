#pragma once
#include "LCTypes.h"

LEVELCONTROLRUNTIME_API bool LCResolve(const FString& Selector, const TMap<FString, FLCEntity>& Entities,
    const FVector& Origin, bool Nearest, bool AllowEmpty, TArray<FString>& Ids, FString& Error);
