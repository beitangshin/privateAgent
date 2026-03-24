# Private Agent

`privateAgent` is a local-first Telegram bot for remote monitoring and low-risk machine interaction.

Right now it is intentionally narrow:

- Telegram polling transport
- allowlisted sender and chat verification
- safe read-only monitoring tools
- a simple note-writing tool
- audit and state storage
- no arbitrary remote shell execution
- DeepSeek cloud planning support

## What This Bot Can Do

The current MVP supports these tool actions:

- `ping`: verify the bot is alive
- `summarize_desktop_status`: return a safe host summary
- `capture_system_info`: return platform and Python diagnostics
- `get_system_health`: return uptime, CPU, memory, and system drive health
- `get_disk_usage`: return all fixed-drive usage
- `get_top_processes`: return the top local processes by memory usage
- `get_network_summary`: return active interface information
- `read_allowed_file`: read a text file under an allowlisted root
- `list_allowed_directory`: list a directory under an allowlisted root
- `take_note`: write a markdown note into the configured notes folder

In Telegram, these show up as chat commands:

- `/ping`
- `/status`
- `/health`
- `/disk`
- `/processes [limit]`
- `/network`
- `/tools`
- `/read <path>`
- `/list <path>`
- `/sysinfo`
- `/note <title> | <body>`
- `/approve <trace_id>`
- `/cancel <trace_id>`

When `PRIVATE_AGENT_MODEL_BACKEND=deepseek_cloud`, you can also send plain natural-language messages such as:

- `现在系统状态如何`
- `帮我检查一下磁盘空间`
- `列出当前最占内存的进程`

## First-Time Setup

1. Create a virtual environment and install dependencies.

```powershell
python -m pip install -e .[dev]
```

2. Fill in [`.env`](D:/projects/privateAgent/.env).

Important values:

- `PRIVATE_AGENT_TELEGRAM_BOT_TOKEN`: your BotFather token
- `PRIVATE_AGENT_ALLOWED_SENDERS`: Telegram user IDs allowed to control the bot
- `PRIVATE_AGENT_ALLOWED_CHAT_IDS`: Telegram chat IDs allowed to talk to the bot
- `PRIVATE_AGENT_ALLOWED_ROOTS`: roots that `/read` and `/list` are allowed to access
- `PRIVATE_AGENT_NOTES_DIR`: folder used by `/note`
- `PRIVATE_AGENT_MODEL_BACKEND`: `mock` or `deepseek_cloud`
- `PRIVATE_AGENT_DEEPSEEK_API_KEY`: required when using `deepseek_cloud`
- `PRIVATE_AGENT_DEEPSEEK_BASE_URL`: DeepSeek API base URL
- `PRIVATE_AGENT_DEEPSEEK_MODEL`: cloud model name
- `PRIVATE_AGENT_MODEL_CALL_LOG_PATH`: where model planning and summary calls are recorded
- `PRIVATE_AGENT_ENABLE_NETWORK_TOOLS`: must be `true` for `/network`

3. If you do not know your Telegram IDs yet:

- send any message to your bot
- run:

```powershell
$env:PYTHONPATH='src'
python -m private_agent.bootstrap_telegram
```

- copy the printed `sender_id` and `chat_id` into [`.env`](D:/projects/privateAgent/.env)

4. Optional but recommended: run tests.

```powershell
python -m pytest
```

## Using The Bot

Once the bot is running, open your bot in Telegram and send commands like:

```text
/ping
/status
/health
/disk
/processes 10
/network
/tools
/sysinfo
/list D:\projects\privateAgent\data\allowed
```

Notes about access:

- `/read` and `/list` only work under `PRIVATE_AGENT_ALLOWED_ROOTS`
- the bot only responds to users and chats listed in `.env`
- `/network` only works when `PRIVATE_AGENT_ENABLE_NETWORK_TOOLS=true`
- when `PRIVATE_AGENT_MODEL_BACKEND=deepseek_cloud`, plain natural-language messages can be planned by DeepSeek and executed locally through safe typed tools
- successful Telegram replies now show user-facing answers only; internal `status` and `trace_id` are kept in logs instead of chat
- if DeepSeek replies with `HTTP 402` or `Insufficient Balance`, your API account needs billing or credits before model mode will work

## Cloud DeepSeek

The cloud DeepSeek backend is now wired into the main natural-language flow.

- backend interface lives under [`models`](D:/projects/privateAgent/src/private_agent/models)
- mock backend remains the default
- cloud backend implementation is in [`deepseek_cloud.py`](D:/projects/privateAgent/src/private_agent/models/deepseek_cloud.py)

To use the cloud backend, set:

```powershell
PRIVATE_AGENT_MODEL_BACKEND=deepseek_cloud
PRIVATE_AGENT_DEEPSEEK_API_KEY=your_key_here
PRIVATE_AGENT_DEEPSEEK_BASE_URL=https://api.deepseek.com
PRIVATE_AGENT_DEEPSEEK_MODEL=deepseek-chat
PRIVATE_AGENT_MODEL_CALL_LOG_PATH=D:\projects\privateAgent\data\model_calls.log
```

How the flow works now:

- Telegram receives your natural-language request
- DeepSeek produces a structured plan
- the local policy layer validates that plan
- the local executor runs only typed safe tools
- DeepSeek summarizes the tool results
- Telegram sends back only the final answer text

## Seeing Model Thinking

If you want to inspect what DeepSeek planned, read:

```powershell
Get-Content .\data\model_calls.log -Tail 5
```

Or follow it live:

```powershell
Get-Content .\data\model_calls.log -Wait
```

Each entry records:

- `kind`: `plan` or `summarize`
- `request_messages`
- `available_tools`
- `raw_content`: the model's raw output
- `reasoning_content`: present when the model/provider returns it
- `parsed_plan`: the structured plan extracted locally
- `status`

Important note:

- `deepseek-chat` often does not expose full reasoning text
- if you want richer reasoning traces later, switch to a reasoning-capable model or DeepSeek thinking mode

## Start The Remote Monitoring Service

This project uses a Telegram polling process as the remote monitoring service.

### Start in the foreground

Use this when you want to watch logs directly in the current terminal:

```powershell
cd D:\projects\privateAgent
$env:PYTHONPATH='src'
python -m private_agent.run_telegram
```

Press `Ctrl+C` to stop it.

### Start in the background

Use this for normal day-to-day use:

```powershell
cd D:\projects\privateAgent
powershell -ExecutionPolicy Bypass -File .\scripts\start_telegram_bot.ps1
```

This script:

- starts the bot as a background Python process
- writes the PID to `data\telegram_bot.pid`
- writes logs to `logs\telegram_bot.out.log` and `logs\telegram_bot.err.log`

## Stop The Remote Monitoring Service

If the bot was started in the foreground, stop it with `Ctrl+C`.

If the bot was started in the background, stop it with:

```powershell
cd D:\projects\privateAgent
powershell -ExecutionPolicy Bypass -File .\scripts\stop_telegram_bot.ps1
```

The stop script first checks `data\telegram_bot.pid`, and if needed it also searches for a matching `python -m private_agent.run_telegram` process.

## Check Remote Monitoring Service Status

To see whether the Telegram monitoring service is currently running, use:

```powershell
cd D:\projects\privateAgent
powershell -ExecutionPolicy Bypass -File .\scripts\status_telegram_bot.ps1
```

This prints:

- whether the bot is running or stopped
- the current PID when running
- the process start time
- whether the PID file exists
- where stdout and stderr logs are stored
- the tail of stderr when there are recent errors

## Logs And Runtime Files

- PID file: [`data/telegram_bot.pid`](D:/projects/privateAgent/data/telegram_bot.pid)
- stdout log: [`logs/telegram_bot.out.log`](D:/projects/privateAgent/logs/telegram_bot.out.log)
- stderr log: [`logs/telegram_bot.err.log`](D:/projects/privateAgent/logs/telegram_bot.err.log)
- audit log: [`data/audit.log`](D:/projects/privateAgent/data/audit.log)
- model call log: [`data/model_calls.log`](D:/projects/privateAgent/data/model_calls.log)
- state store: [`data/state.json`](D:/projects/privateAgent/data/state.json)

These runtime files may not exist until the bot has been started at least once.

## Repo Notes

See [AGENTS.md](D:/projects/privateAgent/AGENTS.md) and the docs folder for the original implementation scope, architecture, and safety constraints.
