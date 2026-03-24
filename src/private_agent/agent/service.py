from __future__ import annotations
import time
import uuid
from dataclasses import dataclass
from typing import Any

from private_agent.audit.logger import AuditEvent, AuditLogger
from private_agent.auth.allowlist import SenderAuthorizer
from private_agent.executor.runtime import Executor
from private_agent.models.base import ModelBackend, ModelMessage, ModelPlanStep
from private_agent.policy.engine import PolicyEngine
from private_agent.storage.state import StateStore
from private_agent.tools.base import ToolRegistry
from private_agent.transport.types import IncomingMessage


@dataclass(slots=True)
class HandleResult:
    trace_id: str
    status: str
    message: str
    data: dict[str, Any] | None = None


class AgentService:
    def __init__(
        self,
        *,
        authorizer: SenderAuthorizer,
        registry: ToolRegistry,
        policy: PolicyEngine,
        executor: Executor,
        audit: AuditLogger,
        state_store: StateStore,
        model_backend: ModelBackend | object,
        enabled_tool_names: set[str] | None = None,
    ) -> None:
        self._authorizer = authorizer
        self._registry = registry
        self._policy = policy
        self._executor = executor
        self._audit = audit
        self._state_store = state_store
        self._model_backend = model_backend
        self._enabled_tool_names = enabled_tool_names

    async def handle_natural_language(self, message: IncomingMessage) -> HandleResult:
        trace_id = uuid.uuid4().hex[:12]
        started = time.perf_counter()
        plan_summary: dict[str, Any] | None = None
        result_state = "handled"
        try:
            self._authorizer.verify(message.sender_id)
            self._authorizer.verify_chat(message.chat_id)
            tools = [
                self._tool_descriptor(spec)
                for spec in self._registry.list_specs()
                if self._is_tool_enabled(spec.name)
            ]
            plan = await self._model_backend.plan(
                [ModelMessage(role="user", content=message.text)],
                tools,
            )
            plan_summary = {
                "intent": plan.intent,
                "requires_confirmation": plan.requires_confirmation,
                "steps": [
                    {"tool_name": step.tool_name, "arguments": step.arguments} for step in plan.steps
                ],
                "response_style": plan.response_style,
                "notes": plan.notes,
            }

            if not plan.steps:
                summary_text = plan.notes or "No local action was required."
                return HandleResult(
                    trace_id=trace_id,
                    status="ok",
                    message=summary_text,
                    data={"intent": plan.intent, "steps_executed": 0},
                )

            if len(plan.steps) > 5:
                return HandleResult(
                    trace_id=trace_id,
                    status="deny",
                    message="model proposed too many steps; refusing plan",
                    data={"intent": plan.intent, "proposed_steps": len(plan.steps)},
                )

            executed, pending = await self._execute_plan_steps(
                plan.steps,
                confirmed=False,
            )
            if pending:
                if pending["status"] != "needs_confirmation":
                    return HandleResult(
                        trace_id=trace_id,
                        status="deny" if pending["status"] == "denied" else "error",
                        message=pending["reason"],
                        data={"intent": plan.intent, "step": pending["tool_name"]},
                    )
                self._save_pending_confirmation(
                    trace_id=trace_id,
                    sender_id=message.sender_id,
                    chat_id=message.chat_id,
                    payload={
                        "kind": "plan",
                        "plan_intent": plan.intent,
                        "plan_notes": plan.notes,
                        "original_text": message.text,
                        "steps": [
                            {"tool_name": step.tool_name, "args": step.arguments} for step in plan.steps
                        ],
                    },
                )
                return HandleResult(
                    trace_id=trace_id,
                    status="allow_with_confirmation",
                    message=(
                        f"model planned '{plan.intent}' but at least one step needs confirmation. "
                        f"Reply with CONFIRM {trace_id}"
                    ),
                    data={"intent": plan.intent, "pending_steps": len(plan.steps)},
                )

            summary = await self._model_backend.summarize(
                [ModelMessage(role="user", content=message.text)],
                {
                    "intent": plan.intent,
                    "plan_notes": plan.notes,
                    "executed_steps": executed,
                },
            )
            return HandleResult(
                trace_id=trace_id,
                status="ok",
                message=summary.content or plan.notes or "plan executed successfully",
                data={
                    "intent": plan.intent,
                    "steps_executed": len(executed),
                    "executed_steps": executed,
                },
            )
        except Exception as exc:  # noqa: BLE001
            result_state = "error"
            return HandleResult(
                trace_id=trace_id,
                status="error",
                message=str(exc),
            )
        finally:
            duration_ms = int((time.perf_counter() - started) * 1000)
            self._audit.write(
                AuditEvent(
                    trace_id=trace_id,
                    module="agent.service",
                    action="natural_language",
                    result=result_state,
                    risk_level="low",
                    duration_ms=duration_ms,
                    details={
                        "sender_id": message.sender_id,
                        "chat_id": message.chat_id,
                        "message_id": message.message_id,
                        "text": message.text,
                        "plan": plan_summary,
                    },
                )
            )

    async def handle_tool_request(
        self, message: IncomingMessage, tool_name: str, args: dict[str, Any]
    ) -> HandleResult:
        trace_id = uuid.uuid4().hex[:12]
        started = time.perf_counter()
        try:
            self._authorizer.verify(message.sender_id)
            self._authorizer.verify_chat(message.chat_id)
            spec = self._registry.get(tool_name)
            decision = self._policy.evaluate(spec)
            if decision.state == "deny":
                return HandleResult(trace_id=trace_id, status="deny", message=decision.reason)
            if decision.state == "allow_with_confirmation":
                self._save_pending_confirmation(
                    trace_id=trace_id,
                    sender_id=message.sender_id,
                    chat_id=message.chat_id,
                    payload={
                        "kind": "tool",
                        "tool_name": tool_name,
                        "args": args,
                    },
                )
                return HandleResult(
                    trace_id=trace_id,
                    status="allow_with_confirmation",
                    message=f"{decision.reason}. Reply with CONFIRM {trace_id}",
                )
            result = await self._executor.run(tool_name, args)
            if result.ok:
                return HandleResult(
                    trace_id=trace_id,
                    status="ok",
                    message="tool execution succeeded",
                    data=result.data,
                )
            return HandleResult(trace_id=trace_id, status="error", message=result.error or "error")
        finally:
            duration_ms = int((time.perf_counter() - started) * 1000)
            self._audit.write(
                AuditEvent(
                    trace_id=trace_id,
                    module="agent.service",
                    action=tool_name,
                    result="handled",
                    risk_level=self._registry.get(tool_name).risk_level,
                    duration_ms=duration_ms,
                    details={
                        "sender_id": message.sender_id,
                        "chat_id": message.chat_id,
                        "message_id": message.message_id,
                    },
                )
            )

    async def approve(self, message: IncomingMessage, trace_id: str) -> HandleResult:
        self._authorizer.verify(message.sender_id)
        self._authorizer.verify_chat(message.chat_id)
        pending = self._load_pending_confirmation(trace_id)
        if not pending:
            return HandleResult(trace_id=trace_id, status="error", message="unknown trace id")
        if pending["sender_id"] != message.sender_id or pending["chat_id"] != message.chat_id:
            return HandleResult(
                trace_id=trace_id,
                status="deny",
                message="trace does not belong to this sender/chat",
            )
        if pending.get("kind") == "plan":
            steps = [
                ModelPlanStep(tool_name=step["tool_name"], arguments=step.get("args", {}))
                for step in pending.get("steps", [])
            ]
            executed, rejected = await self._execute_plan_steps(steps, confirmed=True)
            self._delete_pending_confirmation(trace_id)
            if rejected:
                return HandleResult(
                    trace_id=trace_id,
                    status="deny" if rejected["status"] == "denied" else "error",
                    message=rejected["reason"],
                    data={"rejected_step": rejected},
                )
            summary = await self._model_backend.summarize(
                [ModelMessage(role="user", content=pending.get("original_text", ""))],
                {
                    "intent": pending.get("plan_intent", "confirmed_plan"),
                    "plan_notes": pending.get("plan_notes", ""),
                    "executed_steps": executed,
                },
            )
            return HandleResult(
                trace_id=trace_id,
                status="ok",
                message=summary.content or "confirmed plan execution succeeded",
                data={
                    "intent": pending.get("plan_intent", "confirmed_plan"),
                    "steps_executed": len(executed),
                    "executed_steps": executed,
                },
            )

        result = await self._executor.run(pending["tool_name"], pending["args"])
        self._delete_pending_confirmation(trace_id)
        if result.ok:
            return HandleResult(
                trace_id=trace_id,
                status="ok",
                message="confirmed tool execution succeeded",
                data=result.data,
            )
        return HandleResult(trace_id=trace_id, status="error", message=result.error or "error")

    def cancel(self, message: IncomingMessage, trace_id: str) -> HandleResult:
        self._authorizer.verify(message.sender_id)
        self._authorizer.verify_chat(message.chat_id)
        pending = self._load_pending_confirmation(trace_id)
        if not pending:
            return HandleResult(trace_id=trace_id, status="error", message="unknown trace id")
        self._delete_pending_confirmation(trace_id)
        return HandleResult(trace_id=trace_id, status="cancelled", message="pending action cancelled")

    def list_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": spec.name,
                "description": spec.description,
                "risk_level": spec.risk_level,
                "requires_confirmation": spec.requires_confirmation,
            }
            for spec in self._registry.list_specs()
            if self._is_tool_enabled(spec.name)
        ]

    def model_backend_name(self) -> str:
        return type(self._model_backend).__name__

    def _save_pending_confirmation(
        self,
        *,
        trace_id: str,
        sender_id: str,
        chat_id: str,
        payload: dict[str, Any],
    ) -> None:
        def updater(data: dict[str, Any]) -> dict[str, Any]:
            pending = data.setdefault("pending_confirmations", {})
            pending_payload = {
                "sender_id": sender_id,
                "chat_id": chat_id,
            }
            pending_payload.update(payload)
            pending[trace_id] = pending_payload
            return data

        self._state_store.update(updater)

    def _load_pending_confirmation(self, trace_id: str) -> dict[str, Any] | None:
        data = self._state_store.load()
        return data.get("pending_confirmations", {}).get(trace_id)

    def _delete_pending_confirmation(self, trace_id: str) -> None:
        def updater(data: dict[str, Any]) -> dict[str, Any]:
            pending = data.setdefault("pending_confirmations", {})
            pending.pop(trace_id, None)
            return data

        self._state_store.update(updater)

    def _tool_descriptor(self, spec: Any) -> dict[str, Any]:
        schema = spec.schema()
        schema["risk_level"] = spec.risk_level
        schema["requires_confirmation"] = spec.requires_confirmation
        schema["category"] = spec.category
        return schema

    def _is_tool_enabled(self, tool_name: str) -> bool:
        if self._enabled_tool_names is None:
            return True
        return tool_name in self._enabled_tool_names

    async def _execute_plan_steps(
        self,
        steps: list[ModelPlanStep],
        *,
        confirmed: bool,
    ) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
        executed: list[dict[str, Any]] = []
        for step in steps:
            spec = self._registry.get(step.tool_name)
            decision = self._policy.evaluate(spec)
            if decision.state == "deny":
                return executed, {
                    "status": "denied",
                    "tool_name": step.tool_name,
                    "reason": decision.reason,
                }
            if decision.state == "allow_with_confirmation" and not confirmed:
                return executed, {
                    "status": "needs_confirmation",
                    "tool_name": step.tool_name,
                    "reason": decision.reason,
                }
            result = await self._executor.run(step.tool_name, step.arguments)
            if not result.ok:
                return executed, {
                    "status": "error",
                    "tool_name": step.tool_name,
                    "reason": result.error or "tool execution failed",
                }
            executed.append(
                {
                    "tool_name": step.tool_name,
                    "arguments": step.arguments,
                    "result": result.data,
                }
            )
        return executed, None
