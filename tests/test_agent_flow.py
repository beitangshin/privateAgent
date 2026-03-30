from pathlib import Path

import pytest

from private_agent.agent import AgentService
from private_agent.audit import AuditLogger
from private_agent.auth import SenderAuthorizer
from private_agent.executor import Executor
from private_agent.knowledge import LocalKnowledgeBase
from private_agent.models import MockModelBackend
from private_agent.models.base import ModelMessage, ModelPlan, ModelPlanStep, ModelSummary
from private_agent.policy import PolicyEngine
from private_agent.storage import StateStore
from private_agent.sync import InventorySyncStore
from private_agent.tools import ToolContext, ToolRegistry, build_builtin_tools
from private_agent.transport import IncomingMessage


class FakeModelBackend:
    def __init__(self, *, steps: list[ModelPlanStep], notes: str = "") -> None:
        self._steps = steps
        self._notes = notes
        self.summarize_calls = 0
        self.plan_calls: list[list[ModelMessage]] = []
        self.session_contexts: list[dict[str, object] | None] = []

    async def plan(
        self,
        messages: list[ModelMessage],
        tools: list[dict[str, object]],
        session_context: dict[str, object] | None = None,
    ) -> ModelPlan:
        self.plan_calls.append(messages)
        self.session_contexts.append(session_context)
        return ModelPlan(
            intent="fake_intent",
            requires_confirmation=False,
            steps=self._steps,
            response_style="short_status",
            notes=self._notes,
        )

    async def summarize(
        self, messages: list[ModelMessage], context: dict[str, object]
    ) -> ModelSummary:
        self.summarize_calls += 1
        return ModelSummary(content="fake summary")


def _service(tmp_path: Path) -> AgentService:
    allowed_root = tmp_path / "allowed"
    allowed_root.mkdir()
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    notes_dir = tmp_path / "notes"
    inventory_sync_dir = tmp_path / "inventory_sync"
    registry = ToolRegistry(build_builtin_tools())
    context = ToolContext(
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
    return AgentService(
        authorizer=SenderAuthorizer({"user-1"}, {"chat-1"}),
        registry=registry,
        policy=PolicyEngine(safe_mode=True),
        executor=Executor(registry, context),
        audit=AuditLogger(tmp_path / "audit.log"),
        state_store=StateStore(tmp_path / "state.json"),
        model_backend=MockModelBackend(),
        enabled_tool_names={
            "ping",
            "summarize_desktop_status",
            "read_allowed_file",
            "list_allowed_directory",
            "capture_system_info",
            "get_system_health",
            "get_disk_usage",
            "get_top_processes",
            "web_search",
            "take_note",
            "get_inventory_snapshot",
        },
        conversation_history_messages=6,
        inventory_store=InventorySyncStore(
            root=tmp_path / "inventory_sync",
            knowledge_root=tmp_path / "knowledge_store",
        ),
    )


def _message() -> IncomingMessage:
    return IncomingMessage(
        platform="telegram",
        sender_id="user-1",
        chat_id="chat-1",
        message_id="1",
        text="/note hello | world",
    )


@pytest.mark.anyio
async def test_confirmation_flow(tmp_path: Path) -> None:
    service = _service(tmp_path)
    result = await service.handle_tool_request(
        _message(),
        "take_note",
        {"title": "hello", "body": "world"},
    )
    assert result.status == "allow_with_confirmation"
    approved = await service.approve(_message(), result.trace_id)
    assert approved.status == "ok"


@pytest.mark.anyio
async def test_natural_language_executes_low_risk_plan(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service._model_backend = FakeModelBackend(steps=[ModelPlanStep(tool_name="ping", arguments={})])

    result = await service.handle_natural_language(_message())

    assert result.status == "ok"
    assert result.message == "fake summary"
    assert result.data is not None
    assert result.data["steps_executed"] == 1


@pytest.mark.anyio
async def test_natural_language_requires_confirmation_for_note(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service._model_backend = FakeModelBackend(
        steps=[ModelPlanStep(tool_name="take_note", arguments={"title": "hello", "body": "world"})]
    )

    result = await service.handle_natural_language(_message())

    assert result.status == "allow_with_confirmation"
    approved = await service.approve(_message(), result.trace_id)
    assert approved.status == "ok"


@pytest.mark.anyio
async def test_natural_language_never_sends_web_results_back_to_model(tmp_path: Path) -> None:
    service = _service(tmp_path)
    fake_backend = FakeModelBackend(
        steps=[ModelPlanStep(tool_name="web_search", arguments={"query": "stockholm housing"})],
        notes="Found public search results.",
    )
    service._model_backend = fake_backend
    executor_context = service._executor._context
    executor_context.enable_web_search = True

    original_registry = service._registry
    web_spec = original_registry.get("web_search")

    def fake_web_search(_data, _context):
        return {
            "query": "stockholm housing",
            "results": [
                {
                    "title": "Listing A",
                    "url": "https://example.com/a",
                    "snippet": "4 rooms in central Stockholm",
                    "domain": "example.com",
                }
            ],
            "allowed_domains": [],
        }

    web_spec.handler = fake_web_search

    result = await service.handle_natural_language(_message())

    assert result.status == "ok"
    assert "Listing A" in result.message
    assert "not fed back into the model" in result.message
    assert fake_backend.summarize_calls == 0


def test_set_active_repo_and_read_it_back(tmp_path: Path) -> None:
    service = _service(tmp_path)

    result = service.set_active_repo(_message(), "demo")

    assert result.status == "ok"
    assert service.get_active_repo(_message()) == "demo"


@pytest.mark.anyio
async def test_natural_language_uses_recent_conversation_history(tmp_path: Path) -> None:
    service = _service(tmp_path)
    fake_backend = FakeModelBackend(steps=[])
    service._model_backend = fake_backend
    message = _message()
    message.text = "first question"

    first = await service.handle_natural_language(message)
    assert first.status == "ok"

    follow_up = _message()
    follow_up.message_id = "2"
    follow_up.text = "and then what?"

    second = await service.handle_natural_language(follow_up)

    assert second.status == "ok"
    assert len(fake_backend.plan_calls) == 2
    second_call = fake_backend.plan_calls[1]
    assert [item.content for item in second_call] == [
        "first question",
        "No local action was required.",
        "and then what?",
    ]


def test_reset_conversation_clears_history(tmp_path: Path) -> None:
    service = _service(tmp_path)
    message = _message()
    service._append_conversation_exchange(message, "assistant reply")

    result = service.reset_conversation(message)

    assert result.status == "ok"
    assert service._load_conversation_history(message) == []


def test_web_search_history_is_compacted(tmp_path: Path) -> None:
    service = _service(tmp_path)
    message = _message()
    assistant_text = """Web search results for: stockholm 4 rok till salu
1. Listing A [hemnet.se]
   first snippet
   https://hemnet.se/a
2. Listing B [booli.se]
   second snippet
   https://booli.se/b
Safety notice: external search results were returned directly and were not fed back into the model."""

    service._append_conversation_exchange(message, assistant_text)
    history = service._load_conversation_history(message)

    assert history[-1]["role"] == "assistant"
    assert "Ran web_search for: stockholm 4 rok till salu" in history[-1]["content"]
    assert "Listing A [hemnet.se]" in history[-1]["content"]
    assert "https://hemnet.se/a" not in history[-1]["content"]


@pytest.mark.anyio
async def test_agent_session_tracks_active_goal_across_follow_up(tmp_path: Path) -> None:
    service = _service(tmp_path)
    fake_backend = FakeModelBackend(steps=[])
    service._model_backend = fake_backend

    first = _message()
    first.text = "帮我查斯德哥尔摩核心区 4 rum 房源"
    first.message_id = "10"
    await service.handle_natural_language(first)

    second = _message()
    second.text = "预算改成 1200 万"
    second.message_id = "11"
    await service.handle_natural_language(second)

    assert fake_backend.session_contexts[0]["active_goal"] == ""
    assert fake_backend.session_contexts[1]["active_goal"] == "帮我查斯德哥尔摩核心区 4 rum 房源"


@pytest.mark.anyio
async def test_plan_receives_knowledge_snippets(tmp_path: Path) -> None:
    kb_dir = tmp_path / "knowledge"
    kb_dir.mkdir()
    (kb_dir / "prefs.md").write_text(
        "User preference: prioritize concrete housing listings over market guides.",
        encoding="utf-8",
    )

    service = _service(tmp_path)
    fake_backend = FakeModelBackend(steps=[])
    service._model_backend = fake_backend
    service._knowledge_base = LocalKnowledgeBase(kb_dir, max_snippets=2)

    message = _message()
    message.text = "找房源时优先真实 listing"

    result = await service.handle_natural_language(message)

    assert result.status == "ok"
    snippets = fake_backend.session_contexts[0]["knowledge_snippets"]
    assert snippets
    assert snippets[0]["path"].endswith("prefs.md")


def test_kb_add_writes_under_knowledge_root(tmp_path: Path) -> None:
    service = _service(tmp_path)
    kb_dir = tmp_path / "knowledge"
    kb_dir.mkdir()
    service._knowledge_base = LocalKnowledgeBase(kb_dir, max_snippets=2)
    message = _message()

    result = service.add_knowledge(message, "profile/preferences", "Prefer concise answers.")

    assert result.status == "ok"
    target = kb_dir / "profile" / "preferences.md"
    assert target.exists()
    assert "Prefer concise answers." in target.read_text(encoding="utf-8")


def test_kb_search_returns_matches(tmp_path: Path) -> None:
    service = _service(tmp_path)
    kb_dir = tmp_path / "knowledge"
    kb_dir.mkdir()
    (kb_dir / "profile.md").write_text("Prefer concrete property listings.", encoding="utf-8")
    service._knowledge_base = LocalKnowledgeBase(kb_dir, max_snippets=2)
    message = _message()

    result = service.search_knowledge(message, "concrete listings")

    assert result.status == "ok"
    assert "profile.md" in result.message


def test_inventory_mutation_creates_pending_change(tmp_path: Path) -> None:
    service = _service(tmp_path)
    message = _message()

    result = service.upsert_inventory_item(
        message,
        storage_name="Kitchen",
        box_name="Top Shelf",
        item_name="Milk",
        quantity=2.0,
        unit="Bottle",
        category="Drink",
        note="Fresh",
    )

    assert result.status == "ok"
    snapshot = service._inventory_store.load_snapshot()
    assert snapshot is not None
    assert snapshot["storages"][0]["boxes"][0]["items"][0]["name"] == "Milk"
    changes = service._inventory_store.get_changes(0)["changes"]
    assert changes[0]["type"] == "upsert_item"
