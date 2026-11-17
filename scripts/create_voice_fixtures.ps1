$ErrorActionPreference = 'Stop'
$Repository = Split-Path $PSScriptRoot -Parent
$Directory = Join-Path $Repository 'tests\reports\voice'
New-Item -ItemType Directory -Path $Directory -Force | Out-Null
Add-Type -AssemblyName System.Speech
$Speech = New-Object System.Speech.Synthesis.SpeechSynthesizer
$Speech.SelectVoice('Microsoft Huihui Desktop')
$Speech.Rate = 0
$Format = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)
$Sentences = @{
    'courtyard' = '做一个三面围合，中间有喷泉的庭院。'
    'column' = '在我面前放一根五米高的柱子，然后撤销。'
    'smaller' = '喷泉小一点。'
}
try {
    foreach ($Name in $Sentences.Keys) {
        $Speech.SetOutputToWaveFile((Join-Path $Directory ($Name + '.wav')), $Format)
        $Speech.Speak($Sentences[$Name])
        $Speech.SetOutputToNull()
    }
} finally { $Speech.Dispose() }
$Sentences | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $Directory 'sentences.json') -Encoding utf8
