$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$pidFile = Join-Path $repoRoot "data\telegram_bot.pid"
$stdoutLog = Join-Path $repoRoot "logs\telegram_bot.out.log"
$stderrLog = Join-Path $repoRoot "logs\telegram_bot.err.log"

$runningProcess = $null
$pidSource = $null

if (Test-Path $pidFile) {
    $pidValue = (Get-Content $pidFile -Raw).Trim()
    if ($pidValue) {
        $runningProcess = Get-Process -Id $pidValue -ErrorAction SilentlyContinue
        if ($runningProcess) {
            $pidSource = "pid_file"
        }
    }
}

if (-not $runningProcess) {
    $candidate = Get-CimInstance Win32_Process `
        | Where-Object {
            $_.Name -eq "python.exe" -and
            $_.CommandLine -like "*private_agent.run_telegram*"
        } `
        | Select-Object -First 1

    if ($candidate) {
        $runningProcess = Get-Process -Id $candidate.ProcessId -ErrorAction SilentlyContinue
        if ($runningProcess) {
            $pidSource = "process_scan"
        }
    }
}

if ($runningProcess) {
    Write-Output "privateAgent Telegram bot status: RUNNING"
    Write-Output "PID: $($runningProcess.Id)"
    Write-Output "StartTime: $($runningProcess.StartTime)"
    Write-Output "DetectedBy: $pidSource"
} else {
    Write-Output "privateAgent Telegram bot status: STOPPED"
}

Write-Output "PID file: $pidFile"
Write-Output "PID file exists: $(Test-Path $pidFile)"
Write-Output "stdout log: $stdoutLog"
Write-Output "stdout log exists: $(Test-Path $stdoutLog)"
Write-Output "stderr log: $stderrLog"
Write-Output "stderr log exists: $(Test-Path $stderrLog)"

if (Test-Path $stderrLog) {
    $stderrSize = (Get-Item $stderrLog).Length
    Write-Output "stderr log bytes: $stderrSize"
    if ($stderrSize -gt 0) {
        Write-Output "Recent stderr:"
        Get-Content -Path $stderrLog -Tail 20
    }
}
