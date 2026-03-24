$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$pidFile = Join-Path $repoRoot "data\telegram_bot.pid"

$stopped = $false

if (Test-Path $pidFile) {
    $pidValue = (Get-Content $pidFile -Raw).Trim()
    if ($pidValue) {
        $process = Get-Process -Id $pidValue -ErrorAction SilentlyContinue
        if ($process) {
            Stop-Process -Id $pidValue -Force
            Write-Output "Stopped privateAgent Telegram bot. PID: $pidValue"
            $stopped = $true
        }
    }
    Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
}

if (-not $stopped) {
    $candidateProcesses = Get-CimInstance Win32_Process `
        | Where-Object {
            $_.Name -eq "python.exe" -and
            $_.CommandLine -like "*private_agent.run_telegram*"
        }

    foreach ($candidate in $candidateProcesses) {
        $process = Get-Process -Id $candidate.ProcessId -ErrorAction SilentlyContinue
        if ($process) {
            Stop-Process -Id $candidate.ProcessId -Force -ErrorAction SilentlyContinue
            Write-Output "Stopped privateAgent Telegram bot. PID: $($candidate.ProcessId)"
            $stopped = $true
        }
    }
}

if (-not $stopped) {
    Write-Output "privateAgent Telegram bot is not running."
}
