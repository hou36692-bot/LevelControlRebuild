param([string]$EngineRoot = 'C:\Program Files\Epic Games\UE_5.8', [string]$BundleName = 'LevelControl', [int]$Port = 18870, [string]$Python = '')
$ErrorActionPreference = 'Stop'
$Repository = Split-Path $PSScriptRoot -Parent
$Bundle = Join-Path $Repository ('dist\' + $BundleName)
$Output = Join-Path $Bundle 'Game'
& "$PSScriptRoot\bootstrap.ps1" -EngineRoot $EngineRoot
& "$PSScriptRoot\build.ps1" -EngineRoot $EngineRoot -Target ProjectLC -Configuration Shipping
& (Join-Path $EngineRoot 'Engine\Build\BatchFiles\RunUAT.bat') BuildCookRun `
    "-project=$Repository\project\ProjectLC.uproject" -noP4 -platform=Win64 `
    -clientconfig=Shipping -cook -stage -pak -archive "-archivedirectory=$Output" `
    -map=/Game/Maps/LC_Empty -utf8output -unattended -prereqs -skipbuildeditor
if ($LASTEXITCODE -ne 0) { throw "Cook/package failed: $LASTEXITCODE" }
if (-not $Python) { $Python = Join-Path $Repository '.venv\Scripts\python.exe' }
& $Python "$PSScriptRoot\bundle_companion.py" --engine-root $EngineRoot --output $Bundle --port $Port
if ($LASTEXITCODE -ne 0) { throw 'Companion packaging failed' }
