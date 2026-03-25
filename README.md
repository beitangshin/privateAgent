# Private Agent

`privateAgent` is a local-first Telegram bot for remote monitoring, controlled local execution, and cloud-assisted planning.

The project is intentionally narrow and safe by default:

- Telegram polling transport
- allowlisted sender and chat verification
- safe monitoring tools
- DeepSeek cloud planning and summary support
- local knowledge-base retrieval for durable memory
- structured audit logs and model call logs
- no unrestricted remote shell execution

## Current Capabilities

The current tool surface includes:

- `ping`
- `summarize_desktop_status`
- `capture_system_info`
- `get_system_health`
- `get_disk_usage`
- `get_top_processes`
- `get_network_summary`
- `web_search`
- `read_allowed_file`
- `list_allowed_directory`
- `take_note`

Telegram command examples:

- `/ping`
- `/status`
- `/health`
- `/disk`
- `/processes 10`
- `/network`
- `/web <query>`
- `/kb search <query>`
- `/kb add <path> | <content>`
- `/tools`
- `/read <path>`
- `/list <path>`
- `/sysinfo`
- `/note <title> | <body>`
- `/reset`
- `/approve <trace_id>`
- `/cancel <trace_id>`

When `PRIVATE_AGENT_MODEL_BACKEND=deepseek_cloud`, you can also send natural-language requests such as:

- `现在系统状态如何`
- `帮我检查一下磁盘空间`
- `列出当前最占内存的进程`

## Quick Start

1. Create a virtual environment and install dependencies.

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e .[dev]
```

2. Fill in [`.env`](/home/hil/privateAgent/.env) using [`.env.example`](/home/hil/privateAgent/.env.example).

Important settings:

- `PRIVATE_AGENT_TELEGRAM_BOT_TOKEN`
- `PRIVATE_AGENT_ALLOWED_SENDERS`
- `PRIVATE_AGENT_ALLOWED_CHAT_IDS`
- `PRIVATE_AGENT_ALLOWED_ROOTS`
- `PRIVATE_AGENT_NOTES_DIR`
- `PRIVATE_AGENT_KNOWLEDGE_BASE_DIR`
- `PRIVATE_AGENT_AUDIT_LOG_PATH`
- `PRIVATE_AGENT_MODEL_CALL_LOG_PATH`
- `PRIVATE_AGENT_MODEL_BACKEND`
- `PRIVATE_AGENT_DEEPSEEK_API_KEY`
- `PRIVATE_AGENT_DEEPSEEK_BASE_URL`
- `PRIVATE_AGENT_DEEPSEEK_MODEL`
- `PRIVATE_AGENT_ENABLE_NETWORK_TOOLS`
- `PRIVATE_AGENT_ENABLE_WEB_SEARCH`
- `PRIVATE_AGENT_WEB_SEARCH_ALLOWED_DOMAINS`
- `PRIVATE_AGENT_WEB_SEARCH_MAX_RESULTS`
- `PRIVATE_AGENT_CONVERSATION_HISTORY_MESSAGES`
- `PRIVATE_AGENT_KNOWLEDGE_MAX_SNIPPETS`

3. If you do not know your Telegram IDs yet, send any message to the bot and run:

```bash
export PYTHONPATH=src
python3 -m private_agent.bootstrap_telegram
```

4. Optional but recommended: run tests.

```bash
python3 -m pytest
```

## Running The Bot

### Foreground

```bash
cd /home/pi/privateAgent
export PYTHONPATH=src
python3 -m private_agent.run_telegram
```

Stop with `Ctrl+C`.

### Background

Start:

```bash
cd /home/pi/privateAgent
./scripts/start_telegram_bot.sh
```

Stop:

```bash
cd /home/pi/privateAgent
./scripts/stop_telegram_bot.sh
```

Status:

```bash
cd /home/pi/privateAgent
./scripts/status_telegram_bot.sh
```

The original PowerShell scripts are still available for legacy Windows setups, but the Linux shell scripts are now the primary path for Linux hosts and Raspberry Pi.

## DeepSeek Cloud Mode

To use DeepSeek official API:

```bash
PRIVATE_AGENT_MODEL_BACKEND=deepseek_cloud
PRIVATE_AGENT_DEEPSEEK_API_KEY=your_key_here
PRIVATE_AGENT_DEEPSEEK_BASE_URL=https://api.deepseek.com
PRIVATE_AGENT_DEEPSEEK_MODEL=deepseek-chat
PRIVATE_AGENT_MODEL_CALL_LOG_PATH=/home/pi/privateAgent/data/model_calls.log
```

How the natural-language flow works:

- Telegram receives your request
- local knowledge-base retrieval finds relevant durable memory from disk
- DeepSeek produces a structured plan using chat state plus retrieved knowledge
- the local policy layer validates the plan
- the local executor runs only typed local tools
- DeepSeek summarizes only trusted local tool results
- Telegram shows only the final user-facing answer

Conversation memory:

- each Telegram chat keeps a short rolling history for follow-up questions
- the default history window is controlled by `PRIVATE_AGENT_CONVERSATION_HISTORY_MESSAGES`
- use `/reset` to clear the current chat's conversation memory
- the agent also keeps a compact session state with active goal, recent tool usage, and working-memory summaries so follow-up requests do not start from zero

Knowledge base:

- drop markdown, text, or json documents into `PRIVATE_AGENT_KNOWLEDGE_BASE_DIR`
- the agent retrieves relevant snippets on each natural-language turn
- retrieved snippets are treated as trusted local memory for planning
- this is the long-term memory layer; conversation history is only the short-term layer
- `PRIVATE_AGENT_KNOWLEDGE_MAX_SNIPPETS` controls how many snippets are injected per turn
- use `/kb search <query>` to inspect what the agent can currently retrieve
- use `/kb add <path> | <content>` to append durable notes from Telegram

Suggested knowledge structure:

- `data/knowledge/profile/`
- `data/knowledge/projects/`
- `data/knowledge/procedures/`
- `data/knowledge/references/`

Starter templates created in the repo:

- `/home/pi/privateAgent/data/knowledge/README.md`
- `/home/pi/privateAgent/data/knowledge/profile/user-preferences.md`
- `/home/pi/privateAgent/data/knowledge/projects/project-template.md`
- `/home/pi/privateAgent/data/knowledge/procedures/procedure-template.md`
- `/home/pi/privateAgent/data/knowledge/references/environment-notes.md`

Web search safety boundary:

- `web_search` uses DuckDuckGo result pages only
- it does not fetch the destination pages behind results
- search snippets are treated as untrusted external content
- external search content is never fed back into DeepSeek summary prompts
- if a natural-language request uses `web_search`, the final reply is formatted locally instead of asking the model to summarize those results

If DeepSeek returns `HTTP 402` or `Insufficient Balance`, the API account needs credits or billing.

## Logs And Runtime Files

- PID file: [`data/telegram_bot.pid`](/home/hil/privateAgent/data/telegram_bot.pid)
- stdout log: [`logs/telegram_bot.out.log`](/home/hil/privateAgent/logs/telegram_bot.out.log)
- stderr log: [`logs/telegram_bot.err.log`](/home/hil/privateAgent/logs/telegram_bot.err.log)
- audit log: [`data/audit.log`](/home/hil/privateAgent/data/audit.log)
- model call log: [`data/model_calls.log`](/home/hil/privateAgent/data/model_calls.log)
- state store: [`data/state.json`](/home/hil/privateAgent/data/state.json)
- knowledge directory: [`data/knowledge`](/home/hil/privateAgent/data/knowledge)

Notes:

- `audit.log` records handled requests, tool calls, and policy outcomes
- `model_calls.log` records model planning and summary calls
- plan records may include `session_context`, including retrieved knowledge snippets
- successful Telegram replies hide internal `status` and `trace_id`
- web-search results may appear in the Telegram reply, but they are not replayed into model reasoning context

## Seeing Model Thinking

To inspect recent model calls:

```bash
tail -n 5 ./data/model_calls.log
```

To follow model calls live:

```bash
tail -f ./data/model_calls.log
```

Each model log record may include:

- `kind`
- `request_messages`
- `available_tools`
- `raw_content`
- `reasoning_content`
- `parsed_plan`
- `status`

Important note:

- `deepseek-chat` may not expose full reasoning text
- richer reasoning traces may require a reasoning-capable model or DeepSeek thinking mode

## Remote Programming Direction

The long-term goal is to support remote programming over Telegram, but not by exposing arbitrary shell access.

Recommended design:

- work only inside allowlisted repositories
- bind each session to one active repository
- allow only named repo commands from a command registry
- allow safe repo tools such as read, search, diff, test, commit, and push
- require confirmation for destructive or externalizing actions

This project should not expose raw PowerShell, CMD, or Bash directly from chat input.

Planned remote development tool categories:

- `list_repo_dir`
- `read_repo_file`
- `search_repo`
- `write_repo_patch`
- `run_repo_command`
- `show_repo_diff`
- `git_commit_repo`
- `git_push_repo`

## Security Notes

- `.env`, logs, and runtime state should remain out of git
- secrets should stay local and should not be sent to the cloud model by default
- all side-effecting actions should be confirmation-aware
- remote development should be repo-safe, not system-wide

## More Docs

- [Architecture](/home/hil/privateAgent/docs/architecture.md)
- [Security](/home/hil/privateAgent/docs/security.md)
- [Threat Model](/home/hil/privateAgent/docs/threat-model.md)
- [Project Instructions](/home/hil/privateAgent/AGENTS.md)
