param([string]$EngineRoot = 'C:\Program Files\Epic Games\UE_5.8', [int]$Port = 18844)
$ErrorActionPreference = 'Stop'
$Repository = Split-Path $PSScriptRoot -Parent
& (Join-Path $EngineRoot 'Engine\Binaries\Win64\UnrealEditor.exe') "$Repository\project\ProjectLC.uproject" -game -windowed -ResX=1280 -ResY=720 "-LCPort=$Port" -nosplash -DisablePython
