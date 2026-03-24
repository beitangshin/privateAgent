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
/tools
/read <path>
/list <path>
/sysinfo
/note <title> | <body>
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
    if command == "/tools":
        return ParsedCommand(kind="list_tools")
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
