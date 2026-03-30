from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from private_agent.tools.base import ToolSpec


DecisionState = Literal["allow", "allow_with_confirmation", "deny", "needs_clarification"]


@dataclass(slots=True)
class PolicyDecision:
    state: DecisionState
    reason: str


class PolicyEngine:
    def __init__(self, *, safe_mode: bool) -> None:
        self._safe_mode = safe_mode

    def evaluate(self, tool: ToolSpec) -> PolicyDecision:
        if self._safe_mode and tool.side_effects and tool.risk_level in {"medium", "high"}:
            return PolicyDecision(
                state="allow_with_confirmation",
                reason="safe_mode requires confirmation for side-effect tools",
            )
        if tool.requires_confirmation:
            return PolicyDecision(
                state="allow_with_confirmation",
                reason="tool policy requires explicit confirmation",
            )
        if tool.risk_level == "high":
            if self._safe_mode:
                return PolicyDecision(
                    state="allow_with_confirmation",
                    reason="safe_mode requires confirmation for high-risk tools",
                )
            return PolicyDecision(state="allow", reason="high-risk tool allowed because safe_mode is off")
        return PolicyDecision(state="allow", reason="tool allowed")
