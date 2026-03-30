from pathlib import Path

import pytest

from private_agent.agent import AgentService
from private_agent.audit import AuditLogger
from private_agent.auth import SenderAuthorizer
from private_agent.executor import Executor
from private_agent.knowledge import LocalKnowledgeBase
from private_agent.models import MockModelBackend
from private_agent.models.base import ModelDecision, ModelMessage, ModelSummary
from private_agent.policy import PolicyEngine
from private_agent.storage import StateStore
from private_agent.tools import ToolContext, ToolRegistry, build_builtin_tools
from private_agent.transport import IncomingMessage


class FakeModelBackend:
    def __init__(self, *, decisions: list[ModelDecision]) -> None:
        self._decisions = decisions
        self.summarize_calls = 0
        self.decide_calls: list[list[ModelMessage]] = []
        self.session_contexts: list[dict[str, object] | None] = []
        self.scratchpads: list[list[dict[str, object]] | None] = []

    async def decide_next_step(
        self,
        messages: list[ModelMessage],
        tools: list[dict[str, object]],
        session_context: dict[str, object] | None = None,
        scratchpad: list[dict[str, object]] | None = None,
    ) -> ModelDecision:
        self.decide_calls.append(messages)
        self.session_contexts.append(session_context)
        self.scratchpads.append(scratchpad)
        if self._decisions:
            return self._decisions.pop(0)
        return ModelDecision(thought="done", final_answer="fake summary")

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
    registry = ToolRegistry(build_builtin_tools())
    context = ToolContext(
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
            "inspect_project",
            "project_map",
            "patch_file",
            "capture_system_info",
            "get_system_health",
            "get_disk_usage",
            "get_top_processes",
            "web_search",
            "take_note",
        },
        conversation_history_messages=6,
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
    service._model_backend = FakeModelBackend(
        decisions=[
            ModelDecision(thought="check responsiveness", action="ping", action_input={}),
            ModelDecision(thought="done", final_answer="fake summary"),
        ]
    )

    result = await service.handle_natural_language(_message())

    assert result.status == "ok"
    assert result.message == "fake summary"
    assert result.data is not None
    assert result.data["steps_executed"] == 1


@pytest.mark.anyio
async def test_natural_language_requires_confirmation_for_note(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service._model_backend = FakeModelBackend(
        decisions=[
            ModelDecision(
                thought="save the note",
                action="take_note",
                action_input={"title": "hello", "body": "world"},
            ),
            ModelDecision(thought="done", final_answer="note saved"),
        ]
    )

    result = await service.handle_natural_language(_message())

    assert result.status == "allow_with_confirmation"
    approved = await service.approve(_message(), result.trace_id)
    assert approved.status == "ok"


@pytest.mark.anyio
async def test_natural_language_never_sends_web_results_back_to_model(tmp_path: Path) -> None:
    service = _service(tmp_path)
    fake_backend = FakeModelBackend(
        decisions=[
            ModelDecision(
                thought="search public listings",
                action="web_search",
                action_input={"query": "stockholm housing"},
            )
        ],
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
    assert fake_backend.scratchpads[0] == []


def test_set_active_repo_and_read_it_back(tmp_path: Path) -> None:
    service = _service(tmp_path)

    result = service.set_active_repo(_message(), "demo")

    assert result.status == "ok"
    assert service.get_active_repo(_message()) == "demo"


@pytest.mark.anyio
async def test_local_filesystem_query_bypasses_model_for_named_directory(tmp_path: Path) -> None:
    service = _service(tmp_path)
    fake_backend = FakeModelBackend(
        decisions=[ModelDecision(thought="should not run", final_answer="unexpected")]
    )
    service._model_backend = fake_backend
    target_dir = service._executor._context.allowed_roots[0] / "hiltest"
    target_dir.mkdir()
    (target_dir / "a.txt").write_text("hello", encoding="utf-8")

    message = _message()
    message.text = "你能找到hiltest文件夹下的文件么？？"

    result = await service.handle_natural_language(message)

    assert result.status == "ok"
    assert "a.txt" in result.message
    assert fake_backend.decide_calls == []


@pytest.mark.anyio
async def test_run_query_bypasses_model_for_case_insensitive_named_directory(tmp_path: Path) -> None:
    service = _service(tmp_path)
    fake_backend = FakeModelBackend(
        decisions=[ModelDecision(thought="should not run", final_answer="unexpected")]
    )
    service._model_backend = fake_backend
    target_dir = service._executor._context.allowed_roots[0] / "hilTest"
    target_dir.mkdir()
    (target_dir / "runner.sh").write_text("#!/usr/bin/env bash\n", encoding="utf-8")

    message = _message()
    message.text = "我想要跑完整的hiltest，你帮我看看怎么才能跑？"

    result = await service.handle_natural_language(message)

    assert result.status == "ok"
    assert "hilTest" in result.message
    assert "runner.sh" in result.message
    assert fake_backend.decide_calls == []


def test_find_named_paths_ignores_pytest_temp_matches_and_prefers_home(monkeypatch, tmp_path: Path) -> None:
    service = _service(tmp_path)
    monkeypatch.setenv("HOME", "/home/hil")
    service._executor._context.allowed_roots = (Path("/"),)

    def fake_walk(_root):
        yield "/", ["tmp", "home"], []
        yield "/tmp", ["pytest-of-hil"], []
        yield "/tmp/pytest-of-hil", ["pytest-13"], []
        yield "/tmp/pytest-of-hil/pytest-13", ["test_run_query"], []
        yield "/tmp/pytest-of-hil/pytest-13/test_run_query", ["hilTest"], []
        yield "/home", ["hil"], []
        yield "/home/hil", ["hilTest"], []
        yield "/home/hil/hilTest", [], []

    monkeypatch.setattr("private_agent.agent.service.os.walk", fake_walk)

    matches = service._find_named_paths("hiltest", prefer_directories=True)

    assert matches == ["/home/hil/hilTest"]


@pytest.mark.anyio
async def test_run_query_auto_selects_most_runnable_match_when_multiple_exist(tmp_path: Path) -> None:
    service = _service(tmp_path)
    fake_backend = FakeModelBackend(
        decisions=[ModelDecision(thought="should not run", final_answer="unexpected")]
    )
    service._model_backend = fake_backend

    docs_dir = service._executor._context.allowed_roots[0] / "archive" / "hilTest"
    docs_dir.mkdir(parents=True)
    (docs_dir / "notes.txt").write_text("just notes", encoding="utf-8")

    runnable_dir = service._executor._context.allowed_roots[0] / "workspace" / "hilTest"
    runnable_dir.mkdir(parents=True)
    (runnable_dir / "README.md").write_text("# hilTest\n", encoding="utf-8")
    (runnable_dir / "run.sh").write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    (runnable_dir / "pyproject.toml").write_text("[project]\nname='hiltest'\n", encoding="utf-8")

    message = _message()
    message.text = "我想要跑完整的hiltest，你帮我看看怎么才能跑？"

    result = await service.handle_natural_language(message)

    assert result.status == "ok"
    assert str(runnable_dir) in result.message
    assert "run.sh" in result.message
    assert fake_backend.decide_calls == []


@pytest.mark.anyio
async def test_explicit_edit_request_with_path_reaches_model(tmp_path: Path) -> None:
    service = _service(tmp_path)
    fake_backend = FakeModelBackend(
        decisions=[ModelDecision(thought="done", final_answer="patched")]
    )
    service._model_backend = fake_backend
    target_file = service._executor._context.allowed_roots[0] / "script.sh"
    target_file.write_text("echo old\n", encoding="utf-8")

    message = _message()
    message.text = f"帮我修改 {target_file} 里的 old 为 new"

    result = await service.handle_natural_language(message)

    assert result.status == "ok"
    assert result.message == "patched"
    assert len(fake_backend.decide_calls) == 1


@pytest.mark.anyio
async def test_patching_self_agent_code_clears_conversation_memory(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service._policy = PolicyEngine(safe_mode=False)
    service._agent_root = service._executor._context.allowed_roots[0]
    target_file = service._executor._context.allowed_roots[0] / "agent.py"
    target_file.write_text("print('old')\n", encoding="utf-8")
    message = _message()
    service._append_conversation_exchange(message, "stale context")

    result = await service.handle_tool_request(
        message,
        "patch_file",
        {"path": str(target_file), "old_text": "old", "new_text": "new"},
    )

    assert result.status == "ok"
    assert "Conversation memory was cleared" in result.message
    assert result.data["restart_required"] is True
    assert service._load_conversation_history(message) == []


@pytest.mark.anyio
async def test_react_self_edit_clears_conversation_memory(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service._policy = PolicyEngine(safe_mode=False)
    service._agent_root = service._executor._context.allowed_roots[0]
    target_file = service._executor._context.allowed_roots[0] / "agent.py"
    target_file.write_text("value = 'old'\n", encoding="utf-8")
    service._model_backend = FakeModelBackend(
        decisions=[
            ModelDecision(
                thought="apply the requested edit",
                action="patch_file",
                action_input={"path": str(target_file), "old_text": "old", "new_text": "new"},
            ),
            ModelDecision(thought="done", final_answer="agent updated"),
        ]
    )
    message = _message()
    service._append_conversation_exchange(message, "stale context")

    result = await service.handle_natural_language(message)

    assert result.status == "ok"
    assert "Conversation memory was cleared" in result.message
    assert result.data["restart_required"] is True
    assert service._load_conversation_history(message) == []


@pytest.mark.anyio
async def test_natural_language_uses_recent_conversation_history(tmp_path: Path) -> None:
    service = _service(tmp_path)
    fake_backend = FakeModelBackend(
        decisions=[
            ModelDecision(thought="done", final_answer="No local action was required."),
            ModelDecision(thought="done", final_answer="No local action was required."),
        ]
    )
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
    assert len(fake_backend.decide_calls) == 2
    second_call = fake_backend.decide_calls[1]
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
    fake_backend = FakeModelBackend(
        decisions=[
            ModelDecision(thought="done", final_answer="first"),
            ModelDecision(thought="done", final_answer="second"),
        ]
    )
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
    fake_backend = FakeModelBackend(
        decisions=[ModelDecision(thought="done", final_answer="knowledge used")]
    )
    service._model_backend = fake_backend
    service._knowledge_base = LocalKnowledgeBase(kb_dir, max_snippets=2)

    message = _message()
    message.text = "找房源时优先真实 listing"

    result = await service.handle_natural_language(message)

    assert result.status == "ok"
    snippets = fake_backend.session_contexts[0]["knowledge_snippets"]
    assert snippets
    assert snippets[0]["path"].endswith("prefs.md")


def test_build_task_frame_captures_hidden_steps_for_run_request(tmp_path: Path) -> None:
    service = _service(tmp_path)
    message = _message()
    message.text = "我想要跑完整的hiltest，你帮我看看怎么才能跑？"

    task_frame = service._build_task_frame(message, {})

    assert task_frame["request_kind"] == "execute_or_run"
    assert task_frame["named_targets"] == ["hiltest"]
    assert "infer the best entry point or command" in task_frame["hidden_steps"]
    assert "prefer high-signal tools over broad exploration" in task_frame["decision_hints"]


@pytest.mark.anyio
async def test_react_loop_stops_on_repeated_identical_action(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service._model_backend = FakeModelBackend(
        decisions=[
            ModelDecision(thought="check again", action="ping", action_input={}),
            ModelDecision(thought="check again", action="ping", action_input={}),
            ModelDecision(thought="check again", action="ping", action_input={}),
        ]
    )

    result = await service.handle_natural_language(_message())

    assert result.status == "error"
    assert "repeating the same action" in result.message


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
