#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
pid_file="$repo_root/data/telegram_bot.pid"
stopped=0

if [[ -f "$pid_file" ]]; then
  pid="$(tr -d '[:space:]' < "$pid_file")"
  if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
    kill "$pid" 2>/dev/null || true
    echo "Stopped privateAgent Telegram bot. PID: $pid"
    stopped=1
  fi
  rm -f "$pid_file"
fi

if [[ "$stopped" -eq 0 ]]; then
  pids="$(pgrep -f 'private_agent.run_telegram' || true)"
  if [[ -n "$pids" ]]; then
    while IFS= read -r candidate; do
      [[ -z "$candidate" ]] && continue
      kill "$candidate" 2>/dev/null || true
      echo "Stopped privateAgent Telegram bot. PID: $candidate"
      stopped=1
    done <<< "$pids"
  fi
fi

if [[ "$stopped" -eq 0 ]]; then
  echo "privateAgent Telegram bot is not running."
fi
