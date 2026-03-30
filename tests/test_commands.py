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


def test_parse_inventory_command() -> None:
    parsed = parse_command("/inventory milk")
    assert parsed.kind == "tool"
    assert parsed.tool_name == "get_inventory_snapshot"
    assert parsed.args == {"query": "milk"}


def test_parse_inventory_set_command() -> None:
    parsed = parse_command("/inventory set Kitchen | Top Shelf | Milk | 2 | Bottle | Drink | Fresh")
    assert parsed.kind == "inventory_set_item"
    assert parsed.args == {
        "storage_name": "Kitchen",
        "box_name": "Top Shelf",
        "item_name": "Milk",
        "quantity": 2.0,
        "unit": "Bottle",
        "category": "Drink",
        "note": "Fresh",
    }


def test_parse_inventory_move_command() -> None:
    parsed = parse_command("/inventory move Kitchen | Milk | Door")
    assert parsed.kind == "inventory_move_item"
    assert parsed.args == {
        "storage_name": "Kitchen",
        "item_name": "Milk",
        "target_box_name": "Door",
    }


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
