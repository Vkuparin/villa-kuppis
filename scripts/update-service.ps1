# Update the installed Villa Kuppis service: pull main, refresh dependencies,
# restart the scheduled task and check that the app answers.
# Run in a normal (non-Administrator) PowerShell:  scripts\update-service.ps1
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$config = Join-Path $env:LOCALAPPDATA 'VillaKuppis\service.json'
$port = 8377
if (Test-Path $config) {
    $settings = Get-Content -LiteralPath $config -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($settings.port) { $port = [int]$settings.port }
}

Write-Host '1/4 Pulling main...'
& git pull --ff-only origin main
if ($LASTEXITCODE -ne 0) { throw 'git pull failed (local changes or diverged history). Nothing was restarted.' }

Write-Host '2/4 Updating dependencies...'
& (Join-Path $root '.venv\Scripts\python.exe') -m pip install --quiet -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'pip install failed. Nothing was restarted.' }

Write-Host '3/4 Restarting the VillaKuppis task...'
& schtasks.exe /End /TN VillaKuppis | Out-Null   # fails harmlessly if it was not running
Start-Sleep -Seconds 3
& schtasks.exe /Run /TN VillaKuppis
if ($LASTEXITCODE -ne 0) { throw 'Could not start the VillaKuppis task.' }

Write-Host "4/4 Waiting for http://127.0.0.1:$port ..."
$ok = $false
for ($i = 0; $i -lt 30 -and -not $ok; $i++) {
    Start-Sleep -Seconds 2
    try {
        $r = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$port/api/view" -TimeoutSec 5
        $ok = $r.StatusCode -eq 200
    } catch { }
}
if (-not $ok) {
    Write-Host "The app did not answer. Log: $env:LOCALAPPDATA\VillaKuppis\data\service.log" -ForegroundColor Red
    exit 1
}
Write-Host "Updated to $(& git log --oneline -1). The app is running." -ForegroundColor Green
Write-Host 'Refresh the page in the browser.'
