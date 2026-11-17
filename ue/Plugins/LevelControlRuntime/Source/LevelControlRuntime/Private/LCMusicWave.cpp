#include "LCMusicWave.h"
#include "Misc/FileHelper.h"
#include "Audio.h"

bool ULCMusicWave::Load(const FString& Path)
{
    TArray<uint8> Bytes;
    if (!FFileHelper::LoadFileToArray(Bytes, *Path)) return false;
    FWaveModInfo Info;
    if (!Info.ReadWaveInfo(Bytes.GetData(), Bytes.Num()) || *Info.pChannels != 1 ||
        *Info.pBitsPerSample != 16 || *Info.pSamplesPerSec != 48000 || Info.SampleDataSize == 0) return false;
    PCM.Append(Info.SampleDataStart, Info.SampleDataSize);
    NumChannels = 1; SetSampleRate(48000); SampleByteSize = 2;
    Duration = INDEFINITELY_LOOPING_DURATION;
    SoundGroup = SOUNDGROUP_Music;
    bLooping = false;
    return true;
}

int32 ULCMusicWave::OnGeneratePCMAudio(TArray<uint8>& OutAudio, int32 NumSamples)
{
    if (PCM.IsEmpty()) return 0;
    if (FirstRender.Load() == 0) FirstRender.Store(FPlatformTime::Seconds());
    OutAudio.SetNumUninitialized(NumSamples * 2);
    int32 Written = 0;
    while (Written < OutAudio.Num())
    {
        const int32 Count = FMath::Min(OutAudio.Num() - Written, PCM.Num() - static_cast<int32>(Cursor));
        FMemory::Memcpy(OutAudio.GetData() + Written, PCM.GetData() + Cursor, Count);
        Written += Count; Cursor = (Cursor + Count) % PCM.Num();
    }
    return NumSamples;
}
