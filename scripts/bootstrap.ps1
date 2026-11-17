param([string]$EngineRoot = 'C:\Program Files\Epic Games\UE_5.8')
$ErrorActionPreference = 'Stop'
$Repository = Split-Path $PSScriptRoot -Parent
$Content = Join-Path $Repository 'project\Content'
New-Item -ItemType Directory -Force -Path $Content | Out-Null
foreach ($Pack in @('Characters', 'Input')) {
    $Source = Join-Path $EngineRoot "Templates\TemplateResources\High\$Pack\Content"
    $Destination = Join-Path $Repository ".local-assets\$Pack"
    if (!(Test-Path -LiteralPath $Source)) { throw "Missing engine template content: $Source" }
    if (!(Test-Path -LiteralPath $Destination)) {
        New-Item -ItemType Directory -Force -Path (Split-Path $Destination -Parent) | Out-Null
        Copy-Item -LiteralPath $Source -Destination $Destination -Recurse
    }
    $Link = Join-Path $Content $Pack
    if (!(Test-Path -LiteralPath $Link)) {
        New-Item -ItemType Junction -Path $Link -Target $Destination | Out-Null
    }
}
Write-Output 'Engine template content is ready.'
