#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
pid_file="$repo_root/data/telegram_bot.pid"
stdout_log="$repo_root/logs/telegram_bot.out.log"
stderr_log="$repo_root/logs/telegram_bot.err.log"

running_pid=""
pid_source=""

if [[ -f "$pid_file" ]]; then
  pid_value="$(tr -d '[:space:]' < "$pid_file")"
  if [[ -n "$pid_value" ]] && kill -0 "$pid_value" 2>/dev/null; then
    running_pid="$pid_value"
    pid_source="pid_file"
  fi
fi

if [[ -z "$running_pid" ]]; then
  candidate="$(pgrep -f 'private_agent.run_telegram' | head -n 1 || true)"
  if [[ -n "$candidate" ]]; then
    running_pid="$candidate"
    pid_source="process_scan"
    echo "$candidate" >"$pid_file"
  fi
fi

if [[ -n "$running_pid" ]]; then
  echo "privateAgent Telegram bot status: RUNNING"
  echo "PID: $running_pid"
  echo "DetectedBy: $pid_source"
  if ps -p "$running_pid" -o lstart= >/dev/null 2>&1; then
    echo "StartTime: $(ps -p "$running_pid" -o lstart=)"
  fi
else
  echo "privateAgent Telegram bot status: STOPPED"
fi

echo "PID file: $pid_file"
echo "PID file exists: $( [[ -f "$pid_file" ]] && echo true || echo false )"
echo "stdout log: $stdout_log"
echo "stdout log exists: $( [[ -f "$stdout_log" ]] && echo true || echo false )"
echo "stderr log: $stderr_log"
echo "stderr log exists: $( [[ -f "$stderr_log" ]] && echo true || echo false )"

if [[ -f "$stderr_log" ]]; then
  stderr_size="$(wc -c < "$stderr_log" | tr -d '[:space:]')"
  echo "stderr log bytes: $stderr_size"
  if [[ "$stderr_size" -gt 0 ]]; then
    stderr_epoch="$(stat -c %Y "$stderr_log" 2>/dev/null || echo 0)"
    pid_epoch="$(stat -c %Y "$pid_file" 2>/dev/null || echo 0)"
    if [[ "$stderr_epoch" -ge "$pid_epoch" ]]; then
      echo "Recent stderr:"
      tail -n 20 "$stderr_log"
    else
      echo "Recent stderr: none since last start"
    fi
  else
    echo "Recent stderr: none"
  fi
fi
