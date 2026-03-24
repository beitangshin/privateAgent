from pathlib import Path

import private_agent.tools.builtin as builtin_tools
from private_agent.tools import ToolContext, ToolRegistry, build_builtin_tools


def _context(tmp_path: Path) -> ToolContext:
    allowed_root = tmp_path / "allowed"
    allowed_root.mkdir()
    notes_dir = tmp_path / "notes"
    return ToolContext(
        allowed_roots=(allowed_root.resolve(),),
        notes_dir=notes_dir.resolve(),
        safe_mode=True,
        enable_network_tools=False,
        enable_desktop_tools=False,
        model_backend_name="mock",
    )


def test_read_allowed_file_respects_allowlist(tmp_path: Path) -> None:
    context = _context(tmp_path)
    file_path = context.allowed_roots[0] / "hello.txt"
    file_path.write_text("hello world", encoding="utf-8")

    registry = ToolRegistry(build_builtin_tools())
    spec = registry.get("read_allowed_file")
    result = spec.handler(spec.input_model(path=str(file_path), max_chars=50), context)

    assert result["content"] == "hello world"


def test_take_note_writes_under_notes_dir(tmp_path: Path) -> None:
    context = _context(tmp_path)
    registry = ToolRegistry(build_builtin_tools())
    spec = registry.get("take_note")
    result = spec.handler(spec.input_model(title="daily plan", body="ship MVP"), context)
    note_path = Path(result["path"])

    assert note_path.exists()
    assert note_path.parent == context.notes_dir


def test_get_system_health_returns_mocked_payload(tmp_path: Path, monkeypatch) -> None:
    context = _context(tmp_path)
    registry = ToolRegistry(build_builtin_tools())
    spec = registry.get("get_system_health")

    monkeypatch.setattr(
        builtin_tools,
        "_run_powershell_json",
        lambda command, timeout_sec=15: {
            "computer_name": "DESKTOP-TEST",
            "uptime_hours": 12.5,
            "cpu_load_percent": 18.0,
        },
    )

    result = spec.handler(spec.input_model(), context)

    assert result["computer_name"] == "DESKTOP-TEST"
    assert result["safe_mode"] is True
    assert result["model_backend"] == "mock"


def test_get_network_summary_requires_network_tools_enabled(tmp_path: Path) -> None:
    context = _context(tmp_path)
    registry = ToolRegistry(build_builtin_tools())
    spec = registry.get("get_network_summary")

    try:
        spec.handler(spec.input_model(), context)
    except Exception as exc:  # noqa: BLE001
        assert "network tools are disabled" in str(exc)
    else:
        raise AssertionError("expected network summary to reject when disabled")
