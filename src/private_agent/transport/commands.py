from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class ParsedCommand:
    kind: str
    tool_name: str | None = None
    args: dict[str, Any] | None = None
    trace_id: str | None = None


HELP_TEXT = """Available commands:
/ping
/status
/health
/disk
/processes [limit]
/network
/web <query>
/kb search <query>
/kb add <path> | <content>
/repos
/repo use <name>
/repo status
/repo diff
/repo ls [path]
/repo read <path>
/repo search <pattern>
/repo cmd <command_id>
/tools
/read <path>
/list <path>
/sysinfo
/note <title> | <body>
/reset
/approve <trace_id>
/cancel <trace_id>"""


def parse_command(text: str) -> ParsedCommand:
    stripped = text.strip()
    if not stripped:
        return ParsedCommand(kind="help")

    if stripped.upper().startswith("CONFIRM "):
        return ParsedCommand(kind="approve", trace_id=stripped.split(maxsplit=1)[1].strip())
    if stripped.upper().startswith("CANCEL "):
        return ParsedCommand(kind="cancel", trace_id=stripped.split(maxsplit=1)[1].strip())

    parts = stripped.split(maxsplit=1)
    command = parts[0].lower()
    rest = parts[1].strip() if len(parts) > 1 else ""

    if command in {"/ping", "ping"}:
        return ParsedCommand(kind="tool", tool_name="ping", args={})
    if command in {"/status", "status"}:
        return ParsedCommand(kind="tool", tool_name="summarize_desktop_status", args={})
    if command == "/health":
        return ParsedCommand(kind="tool", tool_name="get_system_health", args={})
    if command == "/disk":
        return ParsedCommand(kind="tool", tool_name="get_disk_usage", args={})
    if command == "/network":
        return ParsedCommand(kind="tool", tool_name="get_network_summary", args={})
    if command == "/web" and rest:
        return ParsedCommand(kind="tool", tool_name="web_search", args={"query": rest})
    if command == "/processes":
        if not rest:
            return ParsedCommand(kind="tool", tool_name="get_top_processes", args={})
        if rest.isdigit():
            return ParsedCommand(
                kind="tool",
                tool_name="get_top_processes",
                args={"limit": int(rest)},
            )
        return ParsedCommand(kind="help")
    if command == "/sysinfo":
        return ParsedCommand(kind="tool", tool_name="capture_system_info", args={})
    if command == "/repos":
        return ParsedCommand(kind="tool", tool_name="list_allowed_repositories", args={})
    if command == "/kb":
        kb_parts = rest.split(maxsplit=1) if rest else []
        subcommand = kb_parts[0].lower() if kb_parts else ""
        remainder = kb_parts[1].strip() if len(kb_parts) > 1 else ""
        if subcommand == "search" and remainder:
            return ParsedCommand(kind="knowledge_search", args={"query": remainder})
        if subcommand == "add" and "|" in remainder:
            path, content = [part.strip() for part in remainder.split("|", maxsplit=1)]
            return ParsedCommand(kind="knowledge_add", args={"path": path, "content": content})
        return ParsedCommand(kind="help")
    if command == "/repo":
        repo_parts = rest.split(maxsplit=1) if rest else []
        subcommand = repo_parts[0].lower() if repo_parts else ""
        remainder = repo_parts[1].strip() if len(repo_parts) > 1 else ""
        if subcommand == "use" and remainder:
            return ParsedCommand(kind="repo_select", args={"repo_name": remainder})
        if subcommand == "status":
            return ParsedCommand(kind="repo_tool", tool_name="get_repo_status", args={})
        if subcommand == "diff":
            return ParsedCommand(kind="repo_tool", tool_name="get_repo_diff", args={})
        if subcommand == "ls":
            return ParsedCommand(
                kind="repo_tool",
                tool_name="list_repo_directory",
                args={"path": remainder} if remainder else {},
            )
        if subcommand == "read" and remainder:
            return ParsedCommand(
                kind="repo_tool",
                tool_name="read_repo_file",
                args={"path": remainder},
            )
        if subcommand == "search" and remainder:
            return ParsedCommand(
                kind="repo_tool",
                tool_name="search_repo",
                args={"pattern": remainder},
            )
        if subcommand == "cmd" and remainder:
            return ParsedCommand(
                kind="repo_tool",
                tool_name="run_repo_command",
                args={"command_id": remainder},
            )
        return ParsedCommand(kind="help")
    if command == "/tools":
        return ParsedCommand(kind="list_tools")
    if command in {"/reset", "/forget"}:
        return ParsedCommand(kind="reset_conversation")
    if command == "/approve" and rest:
        return ParsedCommand(kind="approve", trace_id=rest)
    if command == "/cancel" and rest:
        return ParsedCommand(kind="cancel", trace_id=rest)
    if command == "/read" and rest:
        return ParsedCommand(kind="tool", tool_name="read_allowed_file", args={"path": rest})
    if command == "/list" and rest:
        return ParsedCommand(kind="tool", tool_name="list_allowed_directory", args={"path": rest})
    if command == "/note" and "|" in rest:
        title, body = [part.strip() for part in rest.split("|", maxsplit=1)]
        return ParsedCommand(
            kind="tool",
            tool_name="take_note",
            args={"title": title, "body": body},
        )
    return ParsedCommand(kind="help")
