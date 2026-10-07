param([string]$PythonPath = '', [switch]$ForceBundledPython, [switch]$SkipModels, [switch]$NoShortcut)
$ErrorActionPreference = 'Stop'
$appRoot = $PSScriptRoot
$environmentDir = Join-Path $appRoot '.runtime\desktop'
$pythonExe = Join-Path $environmentDir 'Scripts\python.exe'
$pythonWindowExe = Join-Path $environmentDir 'Scripts\pythonw.exe'

function Initialize-PrivatePython {
    $packageVersion = '3.13.16'
    $packageHash = '95FAD176338F1D6A799E45946488063F4AA61764E6CFF2F132DDEFFBF7A04C34'
    $packageRoot = Join-Path $appRoot ".runtime\python-$packageVersion"
    $privatePython = Join-Path $packageRoot 'tools\python.exe'
    if (Test-Path -LiteralPath $privatePython) { return $privatePython }
    $archive = Join-Path $appRoot ".runtime\python.$packageVersion.nupkg"
    New-Item -ItemType Directory -Path (Split-Path $archive) -Force | Out-Null
    if (-not (Test-Path -LiteralPath $archive) -or (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash -ne $packageHash) {
        Write-Host "Downloading official Python $packageVersion to the application folder..."
        Invoke-WebRequest -Uri "https://api.nuget.org/v3-flatcontainer/python/$packageVersion/python.$packageVersion.nupkg" -OutFile $archive
    }
    if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash -ne $packageHash) { throw 'Python package hash mismatch. No package was executed.' }
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $package = [IO.Compression.ZipFile]::OpenRead($archive)
    try {
        foreach ($entry in $package.Entries) {
            $destination = [IO.Path]::GetFullPath((Join-Path $packageRoot $entry.FullName))
            if (-not $destination.StartsWith($packageRoot + [IO.Path]::DirectorySeparatorChar,[StringComparison]::OrdinalIgnoreCase)) { throw 'Invalid Python package path.' }
            if ($entry.FullName.EndsWith('/')) { New-Item -ItemType Directory -Path $destination -Force | Out-Null; continue }
            New-Item -ItemType Directory -Path ([IO.Path]::GetDirectoryName($destination)) -Force | Out-Null
            [IO.Compression.ZipFileExtensions]::ExtractToFile($entry,$destination,$true)
        }
    } finally { $package.Dispose() }
    & $privatePython -c 'import sys; assert sys.version_info[:3] == (3, 13, 16)'
    if ($LASTEXITCODE -ne 0) { throw 'Could not verify private Python.' }
    return $privatePython
}

if (-not (Test-Path -LiteralPath $pythonExe)) {
    if ($ForceBundledPython) { $PythonPath = Initialize-PrivatePython | Select-Object -Last 1 }
    if (-not $PythonPath) {
        $bundled = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
        if (Test-Path -LiteralPath $bundled) { $PythonPath = $bundled }
        else {
            foreach ($name in @('python', 'python3', 'py')) {
                $candidate = Get-Command $name -ErrorAction SilentlyContinue
                if ($candidate) {
                    & $candidate.Source -c 'import sys; sys.exit(0 if sys.version_info[:2] in [(3,12),(3,13)] else 1)' 2>$null
                    if ($LASTEXITCODE -eq 0) { $PythonPath = $candidate.Source; break }
                }
            }
        }
    }
    if (-not $PythonPath) { $PythonPath = Initialize-PrivatePython | Select-Object -Last 1 }
    & $PythonPath -m venv $environmentDir
    if ($LASTEXITCODE -ne 0) { throw 'Could not prepare the desktop Python environment.' }
}
& $pythonExe -m pip install -r (Join-Path $appRoot 'requirements-desktop.lock.txt')
if ($LASTEXITCODE -ne 0) { throw 'Could not install the desktop window dependency.' }
& $pythonExe -m pip check
if ($LASTEXITCODE -ne 0) { throw 'Dependency compatibility check failed.' }
if (-not $SkipModels) {
    foreach ($script in @('setup_bonsai.py','setup_voice.py','setup_ocr.py')) {
        & $pythonExe (Join-Path $appRoot $script)
        if ($LASTEXITCODE -ne 0) { throw "Could not finish $script. Run this installer again to resume." }
    }
}
if (-not $NoShortcut) {
$desktopPath = [Environment]::GetFolderPath('Desktop')
$shortcutPath = Join-Path $desktopPath 'Local Desk.lnk'
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $pythonWindowExe
$shortcut.Arguments = '"' + (Join-Path $appRoot 'desktop.py') + '"'
$shortcut.WorkingDirectory = $appRoot
$shortcut.IconLocation = (Join-Path $appRoot 'web\local-desk.ico') + ',0'
$shortcut.Description = 'Local AI assistant. Close the window to stop the AI.'
$shortcut.Save()
Write-Output "Desktop shortcut installed: $shortcutPath"
}
Set-Content -LiteralPath (Join-Path $appRoot '.runtime\install-version') -Value '2.0.0' -Encoding ascii
Write-Output 'Local Desk installation finished. Open Desktop.cmd or the desktop icon.'
