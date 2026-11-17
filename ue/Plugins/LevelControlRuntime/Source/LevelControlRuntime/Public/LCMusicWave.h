#pragma once
#include "CoreMinimal.h"
#include "Sound/SoundWaveProcedural.h"
#include "LCMusicWave.generated.h"

UCLASS()
class LEVELCONTROLRUNTIME_API ULCMusicWave : public USoundWaveProcedural
{
    GENERATED_BODY()
public:
    bool Load(const FString& Path);
    virtual int32 OnGeneratePCMAudio(TArray<uint8>& OutAudio, int32 NumSamples) override;
    double RenderStarted() const { return FirstRender.Load(); }
private:
    TArray<uint8> PCM;
    int64 Cursor = 0;
    TAtomic<double> FirstRender {0};
};
