$ErrorActionPreference = 'Stop'
$projectRoot = 'E:\code\python\weitaiAgent'
$pythonExe = Join-Path $projectRoot '.venv\Scripts\python.exe'
$processes = @(Get-CimInstance Win32_Process)
$supervisors = @($processes | Where-Object {
    $_.Name -eq 'python.exe' -and
    ($_.CommandLine -like '*E:\code\python\weitaiAgent\*') -and
    ($_.CommandLine -match 'workers\.dev_all|poetry\.exe.*run weitai-dev')
})
$stopIds = [System.Collections.Generic.HashSet[int]]::new()
foreach ($process in $supervisors) { [void]$stopIds.Add([int]$process.ProcessId) }
do {
    $added = $false
    foreach ($process in $processes) {
        if ($stopIds.Contains([int]$process.ParentProcessId) -and -not $stopIds.Contains([int]$process.ProcessId)) {
            [void]$stopIds.Add([int]$process.ProcessId)
            $added = $true
        }
    }
} while ($added)
if ($stopIds.Count -eq 0) { throw 'No verified project supervisor found; refusing an unscoped restart.' }
$targets = @($processes | Where-Object { $stopIds.Contains([int]$_.ProcessId) })
$listeners = @(Get-NetTCPConnection -State Listen -LocalPort 8100 -ErrorAction SilentlyContinue)
foreach ($listener in $listeners) {
    if (-not $stopIds.Contains([int]$listener.OwningProcess)) { throw 'Port 8100 belongs to another process.' }
}
Write-Output ('Stopping verified project process IDs: ' + (($targets.ProcessId | Sort-Object) -join ', '))
# Stop supervisors first so they cannot respawn an API while its children are being stopped.
foreach ($process in $supervisors) { Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue }
foreach ($process in $targets) { Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Seconds 2
if (Get-NetTCPConnection -State Listen -LocalPort 8100 -ErrorAction SilentlyContinue) { throw 'Old API still listening.' }
$env:PYTHONPATH = "$projectRoot\apps;$projectRoot\packages;$projectRoot"
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$logRoot = Join-Path $projectRoot 'tmp\full-bid-review'
$stdout = Join-Path $logRoot "backend-$stamp.stdout.log"
$stderr = Join-Path $logRoot "backend-$stamp.stderr.log"
$started = Start-Process -FilePath $pythonExe -ArgumentList '-m', 'workers.dev_all' -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru
Write-Output "Started supervisor PID: $($started.Id)"
for ($attempt = 0; $attempt -lt 25; $attempt++) {
    Start-Sleep -Seconds 1
    try {
        $health = Invoke-WebRequest -Uri 'http://127.0.0.1:8100/api/v1/health' -TimeoutSec 2
        if ($health.StatusCode -eq 200) {
            $api = @(Get-NetTCPConnection -State Listen -LocalPort 8100)
            Write-Output "API health HTTP 200; new listener PID: $($api.OwningProcess -join ', ')"
            Write-Output "stdout: $stdout"
            Write-Output "stderr: $stderr"
            exit 0
        }
    } catch { }
}
throw "API not healthy after restart; inspect $stderr"
