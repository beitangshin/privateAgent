#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
pid_file="$repo_root/data/telegram_bot.pid"
stdout_log="$repo_root/logs/telegram_bot.out.log"
stderr_log="$repo_root/logs/telegram_bot.err.log"

running_pid=""
detected_by=""

is_running() {
  local pid="$1"
  kill -0 "$pid" 2>/dev/null
}

if [[ -f "$pid_file" ]]; then
  pid_value="$(tr -d '[:space:]' < "$pid_file")"
  if [[ -n "$pid_value" ]] && is_running "$pid_value"; then
    running_pid="$pid_value"
    detected_by="pid_file"
  fi
fi

if [[ -z "$running_pid" ]]; then
  candidate_pid="$(pgrep -f 'python3? -m private_agent\.run_telegram' | head -n 1 || true)"
  if [[ -n "$candidate_pid" ]] && is_running "$candidate_pid"; then
    running_pid="$candidate_pid"
    detected_by="process_scan"
  fi
fi

if [[ -n "$running_pid" ]]; then
  start_time="$(ps -p "$running_pid" -o lstart= | sed 's/^ *//')"
  echo "privateAgent Telegram bot status: RUNNING"
  echo "PID: $running_pid"
  echo "StartTime: ${start_time:-unknown}"
  echo "DetectedBy: $detected_by"
else
  echo "privateAgent Telegram bot status: STOPPED"
fi

echo "PID file: $pid_file"
if [[ -f "$pid_file" ]]; then
  echo "PID file exists: true"
else
  echo "PID file exists: false"
fi
echo "stdout log: $stdout_log"
if [[ -f "$stdout_log" ]]; then
  echo "stdout log exists: true"
else
  echo "stdout log exists: false"
fi
echo "stderr log: $stderr_log"
if [[ -f "$stderr_log" ]]; then
  echo "stderr log exists: true"
  stderr_size="$(wc -c < "$stderr_log" | tr -d '[:space:]')"
  echo "stderr log bytes: $stderr_size"
  if [[ "${stderr_size:-0}" -gt 0 ]]; then
    echo "Recent stderr:"
    tail -n 20 "$stderr_log"
  fi
else
  echo "stderr log exists: false"
fi
