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
class ModelResponse:
    content: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)


class ModelBackend(Protocol):
    async def plan(
        self, messages: list[ModelMessage], tools: list[dict[str, Any]]
    ) -> ModelPlan:
        ...

    async def summarize(
        self, messages: list[ModelMessage], context: dict[str, Any]
    ) -> ModelSummary:
        ...
