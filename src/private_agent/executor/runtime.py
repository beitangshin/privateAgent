from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass
from typing import Any

from private_agent.tools.base import ToolContext, ToolRegistry


@dataclass(slots=True)
class ExecutionResult:
    tool_name: str
    ok: bool
    data: dict[str, Any] | None = None
    error: str | None = None


class Executor:
    def __init__(self, registry: ToolRegistry, context: ToolContext) -> None:
        self._registry = registry
        self._context = context

    async def run(self, tool_name: str, raw_args: dict[str, Any]) -> ExecutionResult:
        spec = self._registry.get(tool_name)
        validated = spec.input_model.model_validate(raw_args)

        try:
            if inspect.iscoroutinefunction(spec.handler):
                result = await asyncio.wait_for(
                    spec.handler(validated, self._context), timeout=spec.timeout_sec
                )
            else:
                result = await asyncio.wait_for(
                    asyncio.to_thread(spec.handler, validated, self._context),
                    timeout=spec.timeout_sec,
                )
            return ExecutionResult(tool_name=tool_name, ok=True, data=result)
        except Exception as exc:  # noqa: BLE001
            return ExecutionResult(tool_name=tool_name, ok=False, error=str(exc))
