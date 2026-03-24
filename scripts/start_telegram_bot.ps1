$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$pidFile = Join-Path $repoRoot "data\telegram_bot.pid"
$logDir = Join-Path $repoRoot "logs"
$stdoutLog = Join-Path $logDir "telegram_bot.out.log"
$stderrLog = Join-Path $logDir "telegram_bot.err.log"

New-Item -ItemType Directory -Force -Path (Split-Path $pidFile -Parent) | Out-Null
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

if (Test-Path $pidFile) {
    $existingPid = (Get-Content $pidFile -Raw).Trim()
    if ($existingPid) {
        $running = Get-Process -Id $existingPid -ErrorAction SilentlyContinue
        if ($running) {
            Write-Output "privateAgent Telegram bot is already running. PID: $existingPid"
            exit 0
        }
    }
    Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
}

$env:PYTHONPATH = "src"
$process = Start-Process `
    -FilePath "python" `
    -ArgumentList "-m", "private_agent.run_telegram" `
    -WorkingDirectory $repoRoot `
    -RedirectStandardOutput $stdoutLog `
    -RedirectStandardError $stderrLog `
    -PassThru

Set-Content -Path $pidFile -Value $process.Id -Encoding ascii

Write-Output "privateAgent Telegram bot started."
Write-Output "PID: $($process.Id)"
Write-Output "PID file: $pidFile"
Write-Output "stdout log: $stdoutLog"
Write-Output "stderr log: $stderrLog"
