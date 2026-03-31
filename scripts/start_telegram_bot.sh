#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
pid_file="$repo_root/data/telegram_bot.pid"
log_dir="$repo_root/logs"
stdout_log="$log_dir/telegram_bot.out.log"
stderr_log="$log_dir/telegram_bot.err.log"
python_bin="${PYTHON_BIN:-python3}"

mkdir -p "$(dirname "$pid_file")" "$log_dir"

if [[ -f "$pid_file" ]]; then
  existing_pid="$(tr -d '[:space:]' < "$pid_file")"
  if [[ -n "$existing_pid" ]] && kill -0 "$existing_pid" 2>/dev/null; then
    echo "privateAgent Telegram bot is already running. PID: $existing_pid"
    exit 0
  fi
  rm -f "$pid_file"
fi

(
  cd "$repo_root"
  export PYTHONPATH="src${PYTHONPATH:+:$PYTHONPATH}"
  nohup "$python_bin" -m private_agent.run_telegram >>"$stdout_log" 2>>"$stderr_log" &
  echo $! >"$pid_file"
)

echo "privateAgent Telegram bot started."
echo "PID: $(cat "$pid_file")"
echo "PID file: $pid_file"
echo "stdout log: $stdout_log"
echo "stderr log: $stderr_log"
