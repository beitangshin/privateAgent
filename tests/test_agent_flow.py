from pathlib import Path

import pytest

from private_agent.agent import AgentService
from private_agent.audit import AuditLogger
from private_agent.auth import SenderAuthorizer
from private_agent.executor import Executor
from private_agent.models import MockModelBackend
from private_agent.models.base import ModelMessage, ModelPlan, ModelPlanStep, ModelSummary
from private_agent.policy import PolicyEngine
from private_agent.storage import StateStore
from private_agent.tools import ToolContext, ToolRegistry, build_builtin_tools
from private_agent.transport import IncomingMessage


class FakeModelBackend:
    def __init__(self, *, steps: list[ModelPlanStep], notes: str = "") -> None:
        self._steps = steps
        self._notes = notes

    async def plan(self, messages: list[ModelMessage], tools: list[dict[str, object]]) -> ModelPlan:
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
        return ModelSummary(content="fake summary")


def _service(tmp_path: Path) -> AgentService:
    allowed_root = tmp_path / "allowed"
    allowed_root.mkdir()
    notes_dir = tmp_path / "notes"
    registry = ToolRegistry(build_builtin_tools())
    context = ToolContext(
        allowed_roots=(allowed_root.resolve(),),
        notes_dir=notes_dir.resolve(),
        safe_mode=True,
        enable_network_tools=False,
        enable_desktop_tools=False,
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
            "take_note",
        },
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
