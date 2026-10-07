param(
    [ValidateSet('Auto', 'Qwen', 'Ornith', 'Bonsai')][string]$Model = 'Auto',
    [string]$PythonPath = '',
    [switch]$NoBrowser,
    [switch]$SkipModel
)
$ErrorActionPreference = 'Stop'
$appRoot = $PSScriptRoot
$url = 'http://127.0.0.1:8765'

function Find-Python {
    if ($PythonPath) { return $PythonPath }
    foreach ($name in @('python', 'python3', 'py')) {
        $candidate = Get-Command $name -ErrorAction SilentlyContinue
        if ($candidate) {
            & $candidate.Source -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>$null
            if ($LASTEXITCODE -eq 0) { return $candidate.Source }
        }
    }
    $bundled = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
    if (Test-Path -LiteralPath $bundled) { return $bundled }
    throw 'Python 3.10 or newer is required. Install Python, or use -PythonPath.'
}

try {
    $existing = $null
    try { $existing = Invoke-RestMethod "$url/api/bootstrap" -TimeoutSec 2 } catch { }
    if ($existing.application -eq 'local-desk' -or $existing.version -eq '1.0.0') {
        if ($Model -ne 'Auto' -and -not $SkipModel) {
            $body = @{profile=$Model.ToLowerInvariant()} | ConvertTo-Json -Compress
            Invoke-RestMethod "$url/api/profile" -Method Post -Body $body -ContentType 'application/json' -Headers @{'X-App-Token'=$existing.token} -TimeoutSec 240 | Out-Null
        }
        Write-Host "Local Desk is already running: $url"
        if (-not $NoBrowser) { Start-Process $url }
        exit 0
    }
    $pythonExe = Find-Python
    if ($Model -eq 'Auto') {
        $Model = 'Qwen'
        $settingsFile = Join-Path $appRoot '.data\settings.json'
        if (Test-Path -LiteralPath $settingsFile) {
            $settings = Get-Content -Raw -Encoding UTF8 -LiteralPath $settingsFile | ConvertFrom-Json
            if ($settings.model -eq 'local-desk-bonsai') { $Model = 'Bonsai' }
            elseif ($settings.model -eq 'local-desk-ornith') { $Model = 'Ornith' }
        }
    }
    $appArgs = @('-u', (Join-Path $appRoot 'app.py'))
    if (-not $SkipModel) { $appArgs += @('--profile', $Model.ToLowerInvariant()) }
    if (-not $NoBrowser) { $appArgs += '--open' }
    Write-Host "Local Desk: $url / model: $Model"
    Write-Host 'Keep this window open. Press Ctrl+C to stop the application.'
    & $pythonExe @appArgs
    exit $LASTEXITCODE
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    Write-Host 'See README.md for setup and troubleshooting.'
    exit 1
}
