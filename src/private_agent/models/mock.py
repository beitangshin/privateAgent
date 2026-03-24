from __future__ import annotations

from .base import ModelMessage, ModelPlan, ModelSummary


class MockModelBackend:
    async def plan(
        self, messages: list[ModelMessage], tools: list[dict[str, object]]
    ) -> ModelPlan:
        latest = messages[-1].content if messages else ""
        return ModelPlan(
            intent="mock_intent",
            requires_confirmation=False,
            steps=[],
            response_style="short_status",
            notes=f"Mock backend received: {latest}",
        )

    async def summarize(
        self, messages: list[ModelMessage], context: dict[str, object]
    ) -> ModelSummary:
        latest = messages[-1].content if messages else ""
        return ModelSummary(content=f"Mock summary for: {latest}")
