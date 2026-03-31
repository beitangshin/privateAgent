from pathlib import Path

import private_agent.tools.builtin as builtin_tools
from private_agent.sync import InventorySyncStore
from private_agent.run_telegram import _format_result_message
from private_agent.tools import ToolContext, ToolRegistry, build_builtin_tools


def _context(tmp_path: Path) -> ToolContext:
    allowed_root = tmp_path / "allowed"
    allowed_root.mkdir()
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    notes_dir = tmp_path / "notes"
    inventory_sync_dir = tmp_path / "inventory_sync"
    return ToolContext(
        allowed_roots=(allowed_root.resolve(),),
        allowed_repos={"demo": repo_root.resolve()},
        notes_dir=notes_dir.resolve(),
        inventory_sync_dir=inventory_sync_dir.resolve(),
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


def test_get_system_health_returns_mocked_payload(tmp_path: Path, monkeypatch) -> None:
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


def test_get_inventory_snapshot_reads_latest_sync(tmp_path: Path) -> None:
    context = _context(tmp_path)
    context.inventory_sync_dir.mkdir(parents=True, exist_ok=True)
    (context.inventory_sync_dir / "current_inventory.json").write_text(
        """{
  "exported_at": "2026-03-30T12:00:00Z",
  "app_version": "1.0",
  "storages": [
    {
      "name": "Kitchen",
      "boxes": [
        {
          "name": "Top Shelf",
          "items": [
            {"name": "Milk", "quantity": 2, "unit": "Bottle", "category": "Drink", "note": ""}
          ]
        }
      ]
    }
  ]
}""",
        encoding="utf-8",
    )

    registry = ToolRegistry(build_builtin_tools())
    spec = registry.get("get_inventory_snapshot")

    result = spec.handler(spec.input_model(query="milk"), context)

    assert result["available"] is True
    assert result["storage_count"] == 1
    assert result["matches"][0]["storage"] == "Kitchen"
    assert result["matches"][0]["box"] == "Top Shelf"


def test_get_inventory_snapshot_includes_pending_peer_changes(tmp_path: Path) -> None:
    context = _context(tmp_path)
    store = InventorySyncStore(root=context.inventory_sync_dir, knowledge_root=tmp_path / "knowledge")
    store.save_snapshot(
        {
            "exported_at": "2026-03-31T12:00:00Z",
            "app_version": "android",
            "storages": [{"name": "Kitchen", "boxes": [{"name": "Top Shelf", "items": []}]}],
        },
        source="android",
    )
    peer_store = InventorySyncStore(
        root=context.inventory_sync_dir / "peers" / "192.168.1.10",
        knowledge_root=tmp_path / "knowledge",
    )
    peer_store._append_change(
        {
            "type": "upsert_item",
            "storage_name": "Kitchen",
            "box_name": "Top Shelf",
            "item_name": "Eggs",
            "quantity": 2.0,
            "unit": "Box",
            "category": "Food",
            "note": None,
        }
    )

    registry = ToolRegistry(build_builtin_tools())
    spec = registry.get("get_inventory_snapshot")
    result = spec.handler(spec.input_model(query="eggs"), context)

    assert result["available"] is True
    assert result["matches"][0]["name"] == "Eggs"


def test_format_result_message_renders_inventory_query_without_json() -> None:
    class Result:
        status = "ok"
        message = "tool execution succeeded"
        data = {
            "available": True,
            "storage_count": 1,
            "matches": [],
            "query": "鸡蛋",
        }

    formatted = _format_result_message(Result())

    assert formatted == "没有找到“鸡蛋”。"


def test_format_result_message_renders_inventory_matches_without_json() -> None:
    class Result:
        status = "ok"
        message = "tool execution succeeded"
        data = {
            "available": True,
            "storage_count": 1,
            "query": "鸡蛋",
            "matches": [
                {
                    "name": "鸡蛋",
                    "quantity": 2,
                    "unit": "盒",
                    "storage": "冰箱",
                    "box": "冷藏室",
                    "category": "食材",
                    "note": None,
                }
            ],
        }

    formatted = _format_result_message(Result())

    assert "找到了 1 条“鸡蛋”相关库存" in formatted
    assert "鸡蛋: 2盒，位置 冰箱 / 冷藏室，分类 食材" in formatted


def test_inventory_store_prunes_acknowledged_changes(tmp_path: Path) -> None:
    store = InventorySyncStore(root=tmp_path / "inventory", knowledge_root=tmp_path / "knowledge")
    store.upsert_item(
        storage_name="Kitchen",
        box_name="Top Shelf",
        item_name="Milk",
        quantity=2.0,
        unit="Bottle",
        category=None,
        note=None,
    )
    store.upsert_item(
        storage_name="Kitchen",
        box_name="Door",
        item_name="Juice",
        quantity=1.0,
        unit="Bottle",
        category=None,
        note=None,
    )

    first_seq = store.get_changes(0)["changes"][0]["seq"]
    snapshot = store.load_snapshot()
    assert snapshot is not None
    store.save_snapshot(snapshot, acknowledged_change_seq=first_seq, source="android")

    remaining = store.get_changes(0)["changes"]
    assert len(remaining) == 1
    assert remaining[0]["item_name"] == "Juice"
