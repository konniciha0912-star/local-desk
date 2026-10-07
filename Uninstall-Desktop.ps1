param([switch]$RemoveRuntime, [switch]$RemoveHistory, [switch]$NoShortcut, [switch]$WhatIf)
$ErrorActionPreference = 'Stop'
$appRoot = [IO.Path]::GetFullPath($PSScriptRoot)
if (-not (Test-Path -LiteralPath (Join-Path $appRoot 'desktop.py'))) { throw 'Not a Local Desk installation.' }
foreach ($process in Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'") {
    if ($process.CommandLine -and $process.CommandLine.Contains((Join-Path $appRoot 'desktop.py'))) { throw 'Close Local Desk before uninstalling.' }
}
function Remove-ContainedDirectory([string]$relative) {
    $target = [IO.Path]::GetFullPath((Join-Path $appRoot $relative))
    if (-not $target.StartsWith($appRoot + [IO.Path]::DirectorySeparatorChar,[StringComparison]::OrdinalIgnoreCase)) { throw 'Uninstall target is outside the application folder.' }
    if (Test-Path -LiteralPath $target) {
        if ((Get-Item -LiteralPath $target).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Uninstall target is a link. Remove it manually.' }
        $pending = New-Object 'System.Collections.Generic.Stack[string]'
        $pending.Push($target)
        while ($pending.Count) {
            foreach ($entry in Get-ChildItem -LiteralPath $pending.Pop() -Force) {
                if ($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'A nested link was found. Remove it manually before uninstalling.' }
                if ($entry.PSIsContainer) { $pending.Push($entry.FullName) }
            }
        }
        if (-not $WhatIf) { Remove-Item -LiteralPath $target -Recurse -Force }
        Write-Output "Removed: $relative"
    }
}
if (-not $NoShortcut) {
    $shortcutPath = Join-Path ([Environment]::GetFolderPath('Desktop')) 'Local Desk.lnk'
    if (Test-Path -LiteralPath $shortcutPath) {
        $shell = New-Object -ComObject WScript.Shell
        $shortcut = $shell.CreateShortcut($shortcutPath)
        if ($shortcut.WorkingDirectory.TrimEnd('\') -eq $appRoot.TrimEnd('\')) {
            if (-not $WhatIf) { Remove-Item -LiteralPath $shortcutPath -Force }
            Write-Output 'Removed: desktop shortcut'
        }
    }
}
if ($RemoveRuntime) { Remove-ContainedDirectory '.runtime' }
if ($RemoveHistory) { Remove-ContainedDirectory '.data' }
Write-Output 'Application source files remain in this folder.'
if (-not $RemoveHistory) { Write-Output 'History, settings and undo backups are retained. Reinstall to continue.' }
if (-not $RemoveRuntime) { Write-Output 'Downloaded models and runtime are retained.' }
