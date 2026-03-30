from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(slots=True)
class ModelMessage:
    role: str
    content: str


@dataclass(slots=True)
class ModelPlanStep:
    tool_name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ModelPlan:
    intent: str
    requires_confirmation: bool
    steps: list[ModelPlanStep] = field(default_factory=list)
    response_style: str = "short_status"
    notes: str = ""


@dataclass(slots=True)
class ModelSummary:
    content: str


@dataclass(slots=True)
class ModelDecision:
    thought: str = ""
    action: str | None = None
    action_input: dict[str, Any] = field(default_factory=dict)
    final_answer: str | None = None


@dataclass(slots=True)
class ModelResponse:
    content: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)


class ModelBackend(Protocol):
    async def decide_next_step(
        self,
        messages: list[ModelMessage],
        tools: list[dict[str, Any]],
        session_context: dict[str, Any] | None = None,
        scratchpad: list[dict[str, Any]] | None = None,
    ) -> ModelDecision:
        ...

    async def summarize(
        self, messages: list[ModelMessage], context: dict[str, Any]
    ) -> ModelSummary:
        ...
