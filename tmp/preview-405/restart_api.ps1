$ErrorActionPreference = 'Stop'
$projectRoot = 'E:\code\python\weitaiAgent'
$pythonExe = Join-Path $projectRoot '.venv\Scripts\python.exe'
$allProcesses = @(Get-CimInstance Win32_Process)
$supervisors = @($allProcesses | Where-Object {
    $_.Name -eq 'python.exe' -and $_.CommandLine -like '*E:\code\python\weitaiAgent\*' -and
    $_.CommandLine -match 'workers\.dev_all|poetry\.exe.*run weitai-dev'
})
if ($supervisors.Count -gt 0) {
    & (Join-Path $projectRoot 'tmp\full-bid-review\restart_backend.ps1')
    exit $LASTEXITCODE
}
$apiProcesses = @($allProcesses | Where-Object {
    $_.Name -eq 'python.exe' -and $_.CommandLine -like '*E:\code\python\weitaiAgent\*' -and
    $_.CommandLine -match '(?:^|\s)-m\s+(?:api\.main|uvicorn)(?:\s|$)'
})
$apiIds = @($apiProcesses | ForEach-Object { [int]$_.ProcessId })
$listeners = @(Get-NetTCPConnection -State Listen -LocalPort 8100 -ErrorAction SilentlyContinue)
foreach ($listener in $listeners) {
    if ([int]$listener.OwningProcess -notin $apiIds) {
        throw 'Port 8100 belongs to an unverified process; no process was stopped.'
    }
}
foreach ($process in $apiProcesses) {
    Write-Output "Stopping verified project API PID: $($process.ProcessId)"
    Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
}
if ($apiProcesses.Count -gt 0) { Start-Sleep -Seconds 2 }
if (Get-NetTCPConnection -State Listen -LocalPort 8100 -ErrorAction SilentlyContinue) {
    throw 'Port 8100 still in use; refusing to start a duplicate API.'
}
$env:PYTHONPATH = "$projectRoot\apps;$projectRoot\packages;$projectRoot"
$logRoot = Join-Path $projectRoot 'tmp\preview-405'
New-Item -ItemType Directory -Path $logRoot -Force | Out-Null
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$stdout = Join-Path $logRoot "api-$stamp.stdout.log"
$stderr = Join-Path $logRoot "api-$stamp.stderr.log"
$started = Start-Process -FilePath $pythonExe -ArgumentList '-m', 'api.main' -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru
Write-Output "Started project API launcher PID: $($started.Id)"
Write-Output "stdout: $stdout"
Write-Output "stderr: $stderr"
$deadline = (Get-Date).AddSeconds(40)
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Milliseconds 800
    try {
        $health = Invoke-WebRequest -Uri 'http://127.0.0.1:8100/api/v1/health' -TimeoutSec 1
        if ($health.StatusCode -eq 200) {
            $activeListeners = @(Get-NetTCPConnection -State Listen -LocalPort 8100)
            Write-Output "API health HTTP 200; listener PID: $($activeListeners.OwningProcess -join ', ')"
            exit 0
        }
    } catch { }
}
throw "API startup not healthy within 40s; inspect $stderr"
