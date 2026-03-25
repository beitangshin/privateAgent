from pathlib import Path

import private_agent.tools.builtin as builtin_tools
from private_agent.tools import ToolContext, ToolRegistry, build_builtin_tools


def _context(tmp_path: Path) -> ToolContext:
    allowed_root = tmp_path / "allowed"
    allowed_root.mkdir()
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    notes_dir = tmp_path / "notes"
    return ToolContext(
        allowed_roots=(allowed_root.resolve(),),
        allowed_repos={"demo": repo_root.resolve()},
        notes_dir=notes_dir.resolve(),
        safe_mode=True,
        enable_network_tools=False,
        enable_desktop_tools=False,
        enable_web_search=False,
        web_search_allowed_domains=(),
        web_search_max_results=5,
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


def test_read_allowed_file_accepts_file_path_alias(tmp_path: Path) -> None:
    context = _context(tmp_path)
    file_path = context.allowed_roots[0] / "hello_alias.txt"
    file_path.write_text("hello alias", encoding="utf-8")

    registry = ToolRegistry(build_builtin_tools())
    spec = registry.get("read_allowed_file")
    validated = spec.input_model.model_validate({"file_path": str(file_path), "max_chars": 50})
    result = spec.handler(validated, context)

    assert result["content"] == "hello alias"


def test_take_note_writes_under_notes_dir(tmp_path: Path) -> None:
    context = _context(tmp_path)
    registry = ToolRegistry(build_builtin_tools())
    spec = registry.get("take_note")
    result = spec.handler(spec.input_model(title="daily plan", body="ship MVP"), context)
    note_path = Path(result["path"])

    assert note_path.exists()
    assert note_path.parent == context.notes_dir


def test_get_system_health_windows_payload_is_augmented(tmp_path: Path, monkeypatch) -> None:
    context = _context(tmp_path)
    registry = ToolRegistry(build_builtin_tools())
    spec = registry.get("get_system_health")

    monkeypatch.setattr(builtin_tools, "_is_windows", lambda: True)
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


def test_get_system_health_linux_uses_proc_data(tmp_path: Path, monkeypatch) -> None:
    context = _context(tmp_path)
    registry = ToolRegistry(build_builtin_tools())
    spec = registry.get("get_system_health")

    monkeypatch.setattr(builtin_tools, "_is_windows", lambda: False)
    monkeypatch.setattr(
        builtin_tools,
        "_read_meminfo",
        lambda: {"MemTotal": 1024 * 1024, "MemAvailable": 512 * 1024},
    )
    monkeypatch.setattr(
        builtin_tools,
        "shutil",
        type(
            "ShutilStub",
            (),
            {
                "disk_usage": staticmethod(
                    lambda path: type("Usage", (), {"total": 64 * 1024**3, "free": 32 * 1024**3})()
                )
            },
        ),
    )
    monkeypatch.setattr(builtin_tools.os, "getloadavg", lambda: (1.0, 0.5, 0.25))
    monkeypatch.setattr(builtin_tools.os, "cpu_count", lambda: 4)

    result = spec.handler(spec.input_model(), context)

    assert result["system_drive"] == "/"
    assert result["memory_total_gb"] == 1.0
    assert result["memory_free_gb"] == 0.5
    assert result["safe_mode"] is True


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


def test_list_allowed_repositories_returns_repo_map(tmp_path: Path) -> None:
    context = _context(tmp_path)
    registry = ToolRegistry(build_builtin_tools())
    spec = registry.get("list_allowed_repositories")

    result = spec.handler(spec.input_model(), context)

    assert result["repositories"] == [{"name": "demo", "path": str(context.allowed_repos["demo"])}]


def test_web_search_requires_feature_flag(tmp_path: Path) -> None:
    context = _context(tmp_path)
    registry = ToolRegistry(build_builtin_tools())
    spec = registry.get("web_search")

    try:
        spec.handler(spec.input_model(query="stockholm homes"), context)
    except Exception as exc:  # noqa: BLE001
        assert "web search is disabled" in str(exc)
    else:
        raise AssertionError("expected web search to reject when disabled")


def test_web_search_honors_domain_allowlist(tmp_path: Path, monkeypatch) -> None:
    context = _context(tmp_path)
    context.enable_web_search = True
    context.web_search_allowed_domains = ("example.com",)
    registry = ToolRegistry(build_builtin_tools())
    spec = registry.get("web_search")

    monkeypatch.setattr(
        builtin_tools,
        "_search_duckduckgo_results",
        lambda query, limit, allowed_domains, timeout_sec=15: [
            {
                "title": "Allowed",
                "url": "https://example.com/a",
                "snippet": "ok",
                "domain": "example.com",
            }
        ],
    )

    result = spec.handler(spec.input_model(query="demo", max_results=9), context)

    assert result["result_count"] == 1
    assert result["allowed_domains"] == ["example.com"]
    assert result["prompt_injection_protection"]


def test_web_search_coerces_numeric_string_arguments(tmp_path: Path, monkeypatch) -> None:
    context = _context(tmp_path)
    context.enable_web_search = True
    registry = ToolRegistry(build_builtin_tools())
    spec = registry.get("web_search")

    captured: dict[str, object] = {}

    def fake_search(query, *, limit, allowed_domains, timeout_sec=15):
        captured["query"] = query
        captured["limit"] = limit
        captured["allowed_domains"] = allowed_domains
        return []

    monkeypatch.setattr(builtin_tools, "_search_duckduckgo_results", fake_search)

    validated = spec.input_model.model_validate({"query": "stockholm", "max_results": "10"})
    result = spec.handler(validated, context)

    assert result["result_count"] == 0
    assert captured["limit"] == 5


def test_read_repo_file_respects_repo_root(tmp_path: Path) -> None:
    context = _context(tmp_path)
    file_path = context.allowed_repos["demo"] / "README.md"
    file_path.write_text("repo content", encoding="utf-8")
    registry = ToolRegistry(build_builtin_tools())
    spec = registry.get("read_repo_file")

    result = spec.handler(spec.input_model(repo_name="demo", path="README.md"), context)

    assert result["content"] == "repo content"


def test_run_repo_command_blocks_unknown_command(tmp_path: Path) -> None:
    context = _context(tmp_path)
    registry = ToolRegistry(build_builtin_tools())
    spec = registry.get("run_repo_command")

    try:
        spec.handler(spec.input_model(repo_name="demo", command_id="rm_all"), context)
    except Exception as exc:  # noqa: BLE001
        assert "unknown repo command" in str(exc)
    else:
        raise AssertionError("expected unknown repo command to fail")


def test_take_note_accepts_note_content_alias(tmp_path: Path) -> None:
    context = _context(tmp_path)
    registry = ToolRegistry(build_builtin_tools())
    spec = registry.get("take_note")

    validated = spec.input_model.model_validate(
        {"title": "daily", "note_content": "alias body", "unused_field": "ignored"}
    )
    result = spec.handler(validated, context)
    note_path = Path(result["path"])

    assert note_path.exists()
    assert note_path.read_text(encoding="utf-8") == "alias body"


def test_run_repo_command_uses_linux_defaults(tmp_path: Path, monkeypatch) -> None:
    context = _context(tmp_path)
    registry = ToolRegistry(build_builtin_tools())
    spec = registry.get("run_repo_command")

    captured: dict[str, object] = {}

    def fake_run(argv, *, workdir, timeout_sec=30):
        captured["argv"] = argv
        captured["workdir"] = workdir
        return 0, "ok", ""

    monkeypatch.setattr(builtin_tools, "_is_windows", lambda: False)
    monkeypatch.setattr(builtin_tools, "_run_subprocess", fake_run)
    result = spec.handler(spec.input_model(repo_name="demo", command_id="pytest"), context)

    assert result["ok"] is True
    assert captured["argv"] == ["python3", "-m", "pytest", "-q"]
