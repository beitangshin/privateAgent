from private_agent.policy import PolicyEngine
from private_agent.tools import build_builtin_tools


def _tool(name: str):
    return next(tool for tool in build_builtin_tools() if tool.name == name)


def test_safe_mode_requires_confirmation_for_write_tool() -> None:
    decision = PolicyEngine(safe_mode=True).evaluate(_tool("take_note"))
    assert decision.state == "allow_with_confirmation"


def test_safe_mode_allows_read_tool() -> None:
    decision = PolicyEngine(safe_mode=True).evaluate(_tool("read_allowed_file"))
    assert decision.state == "allow"
