from __future__ import annotations

from .base import ModelDecision, ModelMessage, ModelSummary


class MockModelBackend:
    async def decide_next_step(
        self,
        messages: list[ModelMessage],
        tools: list[dict[str, object]],
        session_context: dict[str, object] | None = None,
        scratchpad: list[dict[str, object]] | None = None,
    ) -> ModelDecision:
        latest = messages[-1].content if messages else ""
        prefix = ""
        if session_context and session_context.get("active_goal"):
            prefix = f"[goal={session_context['active_goal']}] "
        scratchpad = scratchpad or []
        if scratchpad:
            return ModelDecision(
                thought="I have enough information.",
                final_answer=f"{prefix}Mock backend completed: {latest}",
            )
        return ModelDecision(
            thought="No tool is needed for the mock backend.",
            final_answer=f"{prefix}Mock backend received: {latest}",
        )

    async def summarize(
        self, messages: list[ModelMessage], context: dict[str, object]
    ) -> ModelSummary:
        latest = messages[-1].content if messages else ""
        return ModelSummary(content=f"Mock summary for: {latest}")
