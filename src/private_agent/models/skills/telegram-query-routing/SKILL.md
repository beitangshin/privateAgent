---
name: telegram-query-routing
description: Use for privateAgent Telegram natural-language queries that need local inspection, search, file reads, repo reads, inventory lookup, or web lookup. Forces tool-first planning instead of answering from memory.
---

# Telegram Query Routing

For Telegram natural-language turns in `privateAgent`, treat query requests as tool-first tasks.

Use this skill when the user is asking to:
- check status, health, disk, processes, network, inventory, or system info
- read, list, or inspect local files
- inspect an allowlisted repository
- search repository content
- look up public web information

Rules:
- For query-style requests, produce at least one tool step.
- Do not answer a query from memory if a local tool can verify it.
- Prefer the narrowest tool that directly answers the question.
- Prefer local tools before web lookup.
- Use `web_search` only when local tools cannot answer the request.
- If the user asks about a repository, prefer repo tools instead of generic filesystem tools.
- If the user asks about inventory, prefer `get_inventory_snapshot`.
- If the user asks for current machine state, prefer `summarize_desktop_status`, `get_system_health`, `get_disk_usage`, `get_top_processes`, `get_network_summary`, or `capture_system_info`.
- If the request is ambiguous but still clearly a query, choose the safest narrow read-only tool first instead of returning zero steps.

Recommended query-tool mapping:
- host status: `summarize_desktop_status`, `get_system_health`, `get_disk_usage`, `get_top_processes`, `get_network_summary`, `capture_system_info`
- local files: `read_allowed_file`, `list_allowed_directory`
- repositories: `list_allowed_repositories`, `list_repo_directory`, `read_repo_file`, `search_repo`, `get_repo_status`, `get_repo_diff`, `run_repo_command`
- inventory: `get_inventory_snapshot`
- public internet lookup: `web_search`

Never do this for a query request:
- return an empty `steps` list when a read-only tool could answer
- invent results that were not retrieved from a tool
- use a broader tool when a narrower one is available
