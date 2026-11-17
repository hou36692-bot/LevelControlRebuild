param(
    [string]$EngineRoot = 'C:\Program Files\Epic Games\UE_5.8',
    [string]$Filter = 'ProjectLC.',
    [int]$Port = 18845
)
$ErrorActionPreference = 'Stop'
$Repository = Split-Path $PSScriptRoot -Parent
$StorePath = Join-Path $Repository ('project\Saved\Tests\' + [guid]::NewGuid().ToString('N') + '.sqlite3')
$Arguments = @(
    "$Repository\project\ProjectLC.uproject", '-game', '-windowed', '-ResX=1280', '-ResY=720',
    '-unattended', '-nosplash', '-NoSound', '-DisablePython', "-LCPort=$Port", "-LCStore=$StorePath",
    "-ExecCmds=`"Automation RunTests $Filter`"",
    "-ReportExportPath=`"$Repository\tests\reports\runtime`"",
    '-TestExit="Automation Test Queue Empty"',
    "-abslog=`"$Repository\project\Saved\Logs\runtime-tests.log`""
)
$TestProcess = Start-Process -FilePath (Join-Path $EngineRoot 'Engine\Binaries\Win64\UnrealEditor.exe') -ArgumentList $Arguments -PassThru -WindowStyle Hidden
$TestProcess.WaitForExit()
$TestProcess.Refresh()
if ($TestProcess.ExitCode -ne 0) { throw "Runtime test process failed: $($TestProcess.ExitCode)" }
$Report = Get-Content -LiteralPath "$Repository\tests\reports\runtime\index.json" -Raw | ConvertFrom-Json
if ($Report.failed -ne 0 -or $Report.succeeded -eq 0) { throw 'Runtime automation tests failed or did not run.' }
Write-Output "Runtime tests passed: $($Report.succeeded)"
