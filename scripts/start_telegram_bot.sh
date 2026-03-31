#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
pid_file="$repo_root/data/telegram_bot.pid"
log_dir="$repo_root/logs"
stdout_log="$log_dir/telegram_bot.out.log"
stderr_log="$log_dir/telegram_bot.err.log"
python_bin="${PYTHON_BIN:-python3}"

mkdir -p "$(dirname "$pid_file")" "$log_dir"
: >"$stdout_log"
: >"$stderr_log"

if [[ -f "$pid_file" ]]; then
  existing_pid="$(tr -d '[:space:]' < "$pid_file")"
  if [[ -n "$existing_pid" ]] && kill -0 "$existing_pid" 2>/dev/null; then
    echo "privateAgent Telegram bot is already running. PID: $existing_pid"
    exit 0
  fi
  rm -f "$pid_file"
fi

cd "$repo_root"
export PYTHONPATH="src${PYTHONPATH:+:$PYTHONPATH}"
nohup "$python_bin" -m private_agent.run_telegram >>"$stdout_log" 2>>"$stderr_log" &
launcher_pid="$!"
bot_pid=""
for _ in 1 2 3 4 5 6 7 8 9 10; do
  candidate="$(pgrep -af "${python_bin} -m private_agent.run_telegram" | awk 'NR==1 {print $1}')"
  if [[ -n "$candidate" ]] && kill -0 "$candidate" 2>/dev/null; then
    bot_pid="$candidate"
    break
  fi
  sleep 0.5
done
echo "${bot_pid:-$launcher_pid}" >"$pid_file"
if [[ -n "${bot_pid:-}" ]] && kill -0 "$bot_pid" 2>/dev/null; then
  : >"$stderr_log"
fi

echo "privateAgent Telegram bot started."
echo "PID: $(cat "$pid_file")"
echo "PID file: $pid_file"
echo "stdout log: $stdout_log"
echo "stderr log: $stderr_log"
