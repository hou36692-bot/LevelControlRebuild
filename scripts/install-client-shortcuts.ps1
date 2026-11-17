$ErrorActionPreference = 'Stop'
$Repository = Split-Path $PSScriptRoot -Parent
$Executable = Join-Path $Repository 'dist\LevelControl\LevelControl.exe'
if (-not (Test-Path -LiteralPath $Executable)) { throw 'Build LevelControl.exe first.' }
$ShortcutShell = New-Object -ComObject WScript.Shell
foreach ($FolderKind in @('Desktop', 'Programs')) {
    $ShortcutFolder = [Environment]::GetFolderPath($FolderKind)
    if (-not $ShortcutFolder) { continue }
    $ShortcutPath = Join-Path $ShortcutFolder 'LevelControl 客户端.lnk'
    $Shortcut = $ShortcutShell.CreateShortcut($ShortcutPath)
    if ($Shortcut.TargetPath -and $Shortcut.TargetPath -ne $Executable) {
        if ([IO.Path]::GetFileName($Shortcut.TargetPath) -ne 'LevelControl.exe') {
            throw "Existing shortcut belongs to another application: $ShortcutPath"
        }
        Copy-Item -LiteralPath $ShortcutPath -Destination ($ShortcutPath + '.pre-3.1.bak')
    }
    $Shortcut.TargetPath = $Executable
    $Shortcut.WorkingDirectory = Split-Path $Executable -Parent
    $Shortcut.IconLocation = "$Executable,0"
    $Shortcut.Description = 'LevelControl 3.2：气泡关卡助手、Codex 与 Claude Code'
    $Shortcut.Save()
    Write-Output $ShortcutPath
}
