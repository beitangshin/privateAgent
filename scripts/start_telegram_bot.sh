#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
pid_file="$repo_root/data/telegram_bot.pid"
log_dir="$repo_root/logs"
stdout_log="$log_dir/telegram_bot.out.log"
stderr_log="$log_dir/telegram_bot.err.log"

if command -v python3 >/dev/null 2>&1; then
  python_bin="python3"
elif command -v python >/dev/null 2>&1; then
  python_bin="python"
else
  echo "python3 or python is required but was not found in PATH" >&2
  exit 1
fi

mkdir -p "$(dirname "$pid_file")" "$log_dir"

is_running() {
  local pid="$1"
  kill -0 "$pid" 2>/dev/null
}

if [[ -f "$pid_file" ]]; then
  existing_pid="$(tr -d '[:space:]' < "$pid_file")"
  if [[ -n "$existing_pid" ]] && is_running "$existing_pid"; then
    echo "privateAgent Telegram bot is already running. PID: $existing_pid"
    exit 0
  fi
  rm -f "$pid_file"
fi

cd "$repo_root"
PYTHONPATH="${PYTHONPATH:-src}" nohup "$python_bin" -m private_agent.run_telegram \
  >>"$stdout_log" 2>>"$stderr_log" &

bot_pid="$!"
printf '%s\n' "$bot_pid" > "$pid_file"

echo "privateAgent Telegram bot started."
echo "PID: $bot_pid"
echo "PID file: $pid_file"
echo "stdout log: $stdout_log"
echo "stderr log: $stderr_log"
