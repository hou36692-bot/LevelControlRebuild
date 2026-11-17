param(
    [string]$EngineRoot = 'C:\Program Files\Epic Games\UE_5.8',
    [ValidateSet('ProjectLCEditor', 'ProjectLC')][string]$Target = 'ProjectLCEditor',
    [ValidateSet('Development', 'Shipping', 'Test')][string]$Configuration = 'Development'
)
$ErrorActionPreference = 'Stop'
$Repository = Split-Path $PSScriptRoot -Parent
& (Join-Path $EngineRoot 'Engine\Build\BatchFiles\Build.bat') $Target Win64 $Configuration "-Project=$Repository\project\ProjectLC.uproject" -WaitMutex -NoHotReloadFromIDE
if ($LASTEXITCODE -ne 0) { throw "Unreal build failed: $LASTEXITCODE" }
