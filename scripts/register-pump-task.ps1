param(
    [Parameter(Mandatory=$true)][string]$TaskFile,
    [Parameter(Mandatory=$true)][string]$TaskUser
)
$ErrorActionPreference = 'Stop'
try {
    if (Get-ScheduledTask -TaskName 'VillaKuppis' -ErrorAction SilentlyContinue) {
        throw 'VillaKuppis already exists. It was not replaced.'
    }
    [xml]$definition = Get-Content -LiteralPath $TaskFile -Raw
    if ($definition.Task.RegistrationInfo.Description -ne 'Villa Kuppis localhost pump app (managed setup v1)' -or
        $definition.Task.Principals.Principal.LogonType -ne 'Password' -or
        $definition.Task.Principals.Principal.RunLevel -ne 'LeastPrivilege') {
        throw 'The task definition does not match the Villa Kuppis setup.'
    }
    Write-Host 'Enter your Windows ACCOUNT PASSWORD below, not your Windows Hello PIN.'
    Write-Host 'Windows Task Scheduler receives and stores it. Villa Kuppis does not read it.'
    & schtasks.exe /Create /TN 'VillaKuppis' /XML $TaskFile /RU $TaskUser /RP '*'
    if ($LASTEXITCODE -ne 0) { throw 'Task registration failed.' }
    & schtasks.exe /Run /TN 'VillaKuppis'
    if ($LASTEXITCODE -ne 0) { throw 'Task was registered but could not be started.' }
    Write-Host 'Villa Kuppis startup task registered and started.'
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
} finally {
    Read-Host 'Press Enter to close'
}
