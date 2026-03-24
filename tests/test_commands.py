from private_agent.transport.commands import parse_command


def test_parse_read_command() -> None:
    parsed = parse_command("/read D:\\allowed\\note.txt")
    assert parsed.kind == "tool"
    assert parsed.tool_name == "read_allowed_file"
    assert parsed.args == {"path": "D:\\allowed\\note.txt"}


def test_parse_note_command() -> None:
    parsed = parse_command("/note daily plan | ship MVP")
    assert parsed.kind == "tool"
    assert parsed.tool_name == "take_note"
    assert parsed.args == {"title": "daily plan", "body": "ship MVP"}


def test_parse_confirm_command() -> None:
    parsed = parse_command("CONFIRM TRACE123")
    assert parsed.kind == "approve"
    assert parsed.trace_id == "TRACE123"


def test_parse_health_command() -> None:
    parsed = parse_command("/health")
    assert parsed.kind == "tool"
    assert parsed.tool_name == "get_system_health"
    assert parsed.args == {}


def test_parse_processes_limit_command() -> None:
    parsed = parse_command("/processes 12")
    assert parsed.kind == "tool"
    assert parsed.tool_name == "get_top_processes"
    assert parsed.args == {"limit": 12}
