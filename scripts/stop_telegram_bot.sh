#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
pid_file="$repo_root/data/telegram_bot.pid"

stopped=0

is_running() {
  local pid="$1"
  kill -0 "$pid" 2>/dev/null
}

stop_pid() {
  local pid="$1"
  if is_running "$pid"; then
    kill "$pid" 2>/dev/null || true
    for _ in $(seq 1 20); do
      if ! is_running "$pid"; then
        echo "Stopped privateAgent Telegram bot. PID: $pid"
        return 0
      fi
      sleep 0.2
    done
    kill -9 "$pid" 2>/dev/null || true
    if ! is_running "$pid"; then
      echo "Stopped privateAgent Telegram bot. PID: $pid"
      return 0
    fi
  fi
  return 1
}

if [[ -f "$pid_file" ]]; then
  pid_value="$(tr -d '[:space:]' < "$pid_file")"
  if [[ -n "$pid_value" ]] && stop_pid "$pid_value"; then
    stopped=1
  fi
  rm -f "$pid_file"
fi

if [[ "$stopped" -eq 0 ]]; then
  while IFS= read -r pid; do
    if [[ -n "$pid" ]] && stop_pid "$pid"; then
      stopped=1
    fi
  done < <(pgrep -f 'python3? -m private_agent\.run_telegram' || true)
fi

if [[ "$stopped" -eq 0 ]]; then
  echo "privateAgent Telegram bot is not running."
fi
