from private_agent.transport.commands import parse_command
from private_agent.run_telegram import _result_requests_restart


def test_parse_read_command() -> None:
    parsed = parse_command("/read /srv/private-agent/allowed/note.txt")
    assert parsed.kind == "tool"
    assert parsed.tool_name == "read_allowed_file"
    assert parsed.args == {"path": "/srv/private-agent/allowed/note.txt"}


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


def test_parse_repo_use_command() -> None:
    parsed = parse_command("/repo use FridgeSystem")
    assert parsed.kind == "repo_select"
    assert parsed.args == {"repo_name": "FridgeSystem"}


def test_parse_repo_search_command() -> None:
    parsed = parse_command("/repo search FoodItemDao")
    assert parsed.kind == "repo_tool"
    assert parsed.tool_name == "search_repo"
    assert parsed.args == {"pattern": "FoodItemDao"}


def test_parse_exec_command() -> None:
    parsed = parse_command("/exec ls -la /tmp")
    assert parsed.kind == "tool"
    assert parsed.tool_name == "run_shell_command"
    assert parsed.args == {"command": "ls -la /tmp"}


def test_parse_find_command() -> None:
    parsed = parse_command("/find hilTest | /home/hil")
    assert parsed.kind == "tool"
    assert parsed.tool_name == "find_paths"
    assert parsed.args == {"pattern": "hilTest", "start_path": "/home/hil"}


def test_parse_inspect_command() -> None:
    parsed = parse_command("/inspect /home/hil/hilTest")
    assert parsed.kind == "tool"
    assert parsed.tool_name == "inspect_project"
    assert parsed.args == {"path": "/home/hil/hilTest"}


def test_parse_project_command() -> None:
    parsed = parse_command("/project /home/hil/privateAgent")
    assert parsed.kind == "tool"
    assert parsed.tool_name == "project_map"
    assert parsed.args == {"path": "/home/hil/privateAgent"}


def test_parse_patch_command() -> None:
    parsed = parse_command("/patch /tmp/demo.txt | old value | new value")
    assert parsed.kind == "tool"
    assert parsed.tool_name == "patch_file"
    assert parsed.args == {
        "path": "/tmp/demo.txt",
        "old_text": "old value",
        "new_text": "new value",
    }


def test_result_requests_restart_only_when_flag_is_set() -> None:
    class Result:
        def __init__(self, data):
            self.data = data

    assert _result_requests_restart(Result({"restart_required": True})) is True
    assert _result_requests_restart(Result({"restart_required": False})) is False
    assert _result_requests_restart(Result(None)) is False


def test_parse_reset_command() -> None:
    parsed = parse_command("/reset")
    assert parsed.kind == "reset_conversation"


def test_parse_kb_search_command() -> None:
    parsed = parse_command("/kb search stockholm housing")
    assert parsed.kind == "knowledge_search"
    assert parsed.args == {"query": "stockholm housing"}


def test_parse_kb_add_command() -> None:
    parsed = parse_command("/kb add profile/user-preferences | Prefer concise answers")
    assert parsed.kind == "knowledge_add"
    assert parsed.args == {
        "path": "profile/user-preferences",
        "content": "Prefer concise answers",
    }
