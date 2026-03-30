from __future__ import annotations

from .base import ModelMessage, ModelPlan, ModelSummary


class MockModelBackend:
    async def plan(
        self,
        messages: list[ModelMessage],
        tools: list[dict[str, object]],
        session_context: dict[str, object] | None = None,
    ) -> ModelPlan:
        latest = messages[-1].content if messages else ""
        prefix = ""
        if session_context and session_context.get("active_goal"):
            prefix = f"[goal={session_context['active_goal']}] "
        return ModelPlan(
            intent="mock_intent",
            requires_confirmation=False,
            steps=[],
            response_style="short_status",
            notes=f"{prefix}Mock backend received: {latest}",
        )

    async def summarize(
        self, messages: list[ModelMessage], context: dict[str, object]
    ) -> ModelSummary:
        latest = messages[-1].content if messages else ""
        return ModelSummary(content=f"Mock summary for: {latest}")
