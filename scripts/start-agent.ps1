param([int]$Port = 18844)
$ErrorActionPreference = 'Stop'
$Repository = Split-Path $PSScriptRoot -Parent
$env:PYTHONUTF8 = '1'
& "$Repository\.venv\Scripts\python.exe" -m lc.agent --port $Port
