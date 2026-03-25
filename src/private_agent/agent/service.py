from __future__ import annotations
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
import json
from pathlib import Path

from private_agent.audit.logger import AuditEvent, AuditLogger
from private_agent.auth.allowlist import SenderAuthorizer
from private_agent.executor.runtime import Executor
from private_agent.knowledge import LocalKnowledgeBase
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
        conversation_history_messages: int = 12,
        knowledge_base: LocalKnowledgeBase | None = None,
    ) -> None:
        self._authorizer = authorizer
        self._registry = registry
        self._policy = policy
        self._executor = executor
        self._audit = audit
        self._state_store = state_store
        self._model_backend = model_backend
        self._enabled_tool_names = enabled_tool_names
        self._conversation_history_messages = conversation_history_messages
        self._knowledge_base = knowledge_base

    async def handle_natural_language(self, message: IncomingMessage) -> HandleResult:
        trace_id = uuid.uuid4().hex[:12]
        started = time.perf_counter()
        plan_summary: dict[str, Any] | None = None
        result_state = "handled"
        session_context = self._session_context(message)
        knowledge_context = self._knowledge_context(message, session_context)
        session_context["knowledge_snippets"] = knowledge_context
        conversation = self._conversation_messages(message)
        try:
            self._authorizer.verify(message.sender_id)
            self._authorizer.verify_chat(message.chat_id)
            tools = [
                self._tool_descriptor(spec)
                for spec in self._registry.list_specs()
                if self._is_tool_enabled(spec.name)
            ]
            plan = await self._model_backend.plan(
                conversation,
                tools,
                session_context=session_context,
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
                self._append_conversation_exchange(message, summary_text)
                self._update_agent_session(
                    message,
                    active_goal=self._derive_active_goal(message.text, session_context),
                    plan_intent=plan.intent,
                    plan_notes=plan.notes,
                    tool_names=[],
                    assistant_text=summary_text,
                )
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

            summary_context = self._sanitize_executed_steps_for_model(executed)
            if summary_context is None:
                local_message = self._render_local_execution_summary(plan.intent, plan.notes, executed)
                self._append_conversation_exchange(message, local_message)
                self._update_agent_session(
                    message,
                    active_goal=self._derive_active_goal(message.text, session_context),
                    plan_intent=plan.intent,
                    plan_notes=plan.notes,
                    tool_names=[step["tool_name"] for step in executed],
                    assistant_text=local_message,
                )
                return HandleResult(
                    trace_id=trace_id,
                    status="ok",
                    message=local_message,
                    data={
                        "intent": plan.intent,
                        "steps_executed": len(executed),
                        "executed_steps": executed,
                    },
                )

            summary = await self._model_backend.summarize(
                conversation,
                {
                    "intent": plan.intent,
                    "plan_notes": plan.notes,
                    "executed_steps": summary_context,
                },
            )
            final_message = summary.content or plan.notes or "plan executed successfully"
            self._append_conversation_exchange(message, final_message)
            self._update_agent_session(
                message,
                active_goal=self._derive_active_goal(message.text, session_context),
                plan_intent=plan.intent,
                plan_notes=plan.notes,
                tool_names=[step["tool_name"] for step in executed],
                assistant_text=final_message,
            )
            return HandleResult(
                trace_id=trace_id,
                status="ok",
                message=final_message,
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
                        "knowledge_paths": [item["path"] for item in knowledge_context],
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

    async def handle_active_repo_tool_request(
        self, message: IncomingMessage, tool_name: str, args: dict[str, Any]
    ) -> HandleResult:
        active_repo = self.get_active_repo(message)
        if not active_repo:
            return HandleResult(
                trace_id=uuid.uuid4().hex[:12],
                status="error",
                message="no active repository selected. Use /repo use <name> first",
            )
        repo_args = dict(args)
        repo_args["repo_name"] = active_repo
        return await self.handle_tool_request(message, tool_name, repo_args)

    def set_active_repo(self, message: IncomingMessage, repo_name: str) -> HandleResult:
        trace_id = uuid.uuid4().hex[:12]
        self._authorizer.verify(message.sender_id)
        self._authorizer.verify_chat(message.chat_id)
        available = self._available_repo_names()
        if repo_name not in available:
            return HandleResult(
                trace_id=trace_id,
                status="error",
                message=f"unknown repository '{repo_name}'. Available: {', '.join(available) or 'none'}",
            )

        key = self._repo_session_key(message)

        def updater(data: dict[str, Any]) -> dict[str, Any]:
            sessions = data.setdefault("active_repositories", {})
            sessions[key] = repo_name
            return data

        self._state_store.update(updater)
        return HandleResult(
            trace_id=trace_id,
            status="ok",
            message=f"active repository set to {repo_name}",
            data={"repo_name": repo_name},
        )

    def get_active_repo(self, message: IncomingMessage) -> str | None:
        self._authorizer.verify(message.sender_id)
        self._authorizer.verify_chat(message.chat_id)
        data = self._state_store.load()
        return data.get("active_repositories", {}).get(self._repo_session_key(message))

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
            summary_context = self._sanitize_executed_steps_for_model(executed)
            if summary_context is None:
                return HandleResult(
                    trace_id=trace_id,
                    status="ok",
                    message=self._render_local_execution_summary(
                        pending.get("plan_intent", "confirmed_plan"),
                        pending.get("plan_notes", ""),
                        executed,
                    ),
                    data={
                        "intent": pending.get("plan_intent", "confirmed_plan"),
                        "steps_executed": len(executed),
                        "executed_steps": executed,
                    },
                )

            summary = await self._model_backend.summarize(
                [ModelMessage(role="user", content=pending.get("original_text", ""))],
                {
                    "intent": pending.get("plan_intent", "confirmed_plan"),
                    "plan_notes": pending.get("plan_notes", ""),
                    "executed_steps": summary_context,
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

    def reset_conversation(self, message: IncomingMessage) -> HandleResult:
        trace_id = uuid.uuid4().hex[:12]
        self._authorizer.verify(message.sender_id)
        self._authorizer.verify_chat(message.chat_id)

        def updater(data: dict[str, Any]) -> dict[str, Any]:
            histories = data.setdefault("conversation_histories", {})
            histories.pop(self._conversation_session_key(message), None)
            sessions = data.setdefault("agent_sessions", {})
            sessions.pop(self._agent_session_key(message), None)
            return data

        self._state_store.update(updater)
        return HandleResult(
            trace_id=trace_id,
            status="ok",
            message="conversation memory cleared for this chat",
        )

    def search_knowledge(self, message: IncomingMessage, query: str) -> HandleResult:
        trace_id = uuid.uuid4().hex[:12]
        self._authorizer.verify(message.sender_id)
        self._authorizer.verify_chat(message.chat_id)
        if self._knowledge_base is None:
            return HandleResult(trace_id=trace_id, status="error", message="knowledge base is not configured")
        query_text = query.strip()
        if not query_text:
            return HandleResult(trace_id=trace_id, status="error", message="knowledge search query is required")
        snippets = self._knowledge_base.retrieve(query_text)
        if not snippets:
            return HandleResult(
                trace_id=trace_id,
                status="ok",
                message=f"No knowledge snippets matched: {query_text}",
                data={"query": query_text, "matches": []},
            )
        lines = [f"Knowledge matches for: {query_text}"]
        matches: list[dict[str, Any]] = []
        for index, snippet in enumerate(snippets, start=1):
            relative_path = self._knowledge_relative_path(snippet.path)
            excerpt = " ".join(snippet.text.split())
            lines.append(f"{index}. {relative_path} (score={snippet.score})")
            lines.append(f"   {excerpt}")
            matches.append(
                {
                    "path": relative_path,
                    "score": snippet.score,
                    "text": snippet.text,
                }
            )
        return HandleResult(
            trace_id=trace_id,
            status="ok",
            message="\n".join(lines),
            data={"query": query_text, "matches": matches},
        )

    def add_knowledge(self, message: IncomingMessage, raw_path: str, content: str) -> HandleResult:
        trace_id = uuid.uuid4().hex[:12]
        self._authorizer.verify(message.sender_id)
        self._authorizer.verify_chat(message.chat_id)
        if self._knowledge_base is None:
            return HandleResult(trace_id=trace_id, status="error", message="knowledge base is not configured")
        relative_path = raw_path.strip()
        body = content.strip()
        if not relative_path or not body:
            return HandleResult(
                trace_id=trace_id,
                status="error",
                message="usage: /kb add <path> | <content>",
            )

        target_path = self._resolve_knowledge_path(relative_path)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).isoformat()
        heading = f"\n\n## Entry {timestamp}\n" if target_path.exists() else ""
        with target_path.open("a", encoding="utf-8") as handle:
            if heading:
                handle.write(heading)
            handle.write(body)
            handle.write("\n")

        return HandleResult(
            trace_id=trace_id,
            status="ok",
            message=f"Knowledge saved to {self._knowledge_relative_path(str(target_path))}",
            data={"path": self._knowledge_relative_path(str(target_path))},
        )

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

    def _available_repo_names(self) -> list[str]:
        list_repo_spec = self._registry.get("list_allowed_repositories")
        context = getattr(self._executor, "_context")
        result = list_repo_spec.handler(list_repo_spec.input_model(), context)
        return [entry["name"] for entry in result.get("repositories", [])]

    def _repo_session_key(self, message: IncomingMessage) -> str:
        return f"{message.sender_id}:{message.chat_id}"

    def _conversation_session_key(self, message: IncomingMessage) -> str:
        return self._repo_session_key(message)

    def _agent_session_key(self, message: IncomingMessage) -> str:
        return self._conversation_session_key(message)

    def _session_context(self, message: IncomingMessage) -> dict[str, Any]:
        data = self._state_store.load()
        session = data.get("agent_sessions", {}).get(self._agent_session_key(message), {})
        if not isinstance(session, dict):
            return {}
        return {
            "active_goal": session.get("active_goal", ""),
            "last_intent": session.get("last_intent", ""),
            "last_plan_notes": session.get("last_plan_notes", ""),
            "recent_tool_names": session.get("recent_tool_names", []),
            "working_memory": session.get("working_memory", []),
        }

    def _knowledge_context(
        self, message: IncomingMessage, session_context: dict[str, Any]
    ) -> list[dict[str, Any]]:
        if self._knowledge_base is None:
            return []
        query_parts = [message.text.strip()]
        active_goal = str(session_context.get("active_goal", "")).strip()
        if active_goal and active_goal not in query_parts:
            query_parts.append(active_goal)
        snippets = self._knowledge_base.retrieve("\n".join(part for part in query_parts if part))
        return [
            {
                "path": snippet.path,
                "text": snippet.text,
                "score": snippet.score,
            }
            for snippet in snippets
        ]

    def _resolve_knowledge_path(self, raw_path: str) -> Path:
        if self._knowledge_base is None:
            raise RuntimeError("knowledge base is not configured")
        candidate = Path(raw_path.strip().replace("\\", "/"))
        if not candidate.suffix:
            candidate = candidate.with_suffix(".md")
        target = (self._knowledge_base.root / candidate).resolve()
        try:
            target.relative_to(self._knowledge_base.root)
        except ValueError as exc:
            raise RuntimeError("knowledge path is outside knowledge base root") from exc
        return target

    def _knowledge_relative_path(self, path: str) -> str:
        if self._knowledge_base is None:
            return path
        candidate = Path(path)
        try:
            return str(candidate.relative_to(self._knowledge_base.root)).replace("\\", "/")
        except ValueError:
            return str(candidate)

    def _conversation_messages(self, message: IncomingMessage) -> list[ModelMessage]:
        history = self._load_conversation_history(message)
        messages = [
            ModelMessage(role=str(entry.get("role", "user")), content=str(entry.get("content", "")))
            for entry in history
            if entry.get("content")
        ]
        messages.append(ModelMessage(role="user", content=message.text))
        return messages

    def _load_conversation_history(self, message: IncomingMessage) -> list[dict[str, str]]:
        if self._conversation_history_messages <= 0:
            return []
        data = self._state_store.load()
        return data.get("conversation_histories", {}).get(
            self._conversation_session_key(message), []
        )

    def _append_conversation_exchange(self, message: IncomingMessage, assistant_text: str) -> None:
        if self._conversation_history_messages <= 0:
            return
        stored_assistant_text = self._compact_history_text(assistant_text)

        def updater(data: dict[str, Any]) -> dict[str, Any]:
            histories = data.setdefault("conversation_histories", {})
            key = self._conversation_session_key(message)
            history = histories.get(key, [])
            history.extend(
                [
                    {"role": "user", "content": message.text},
                    {"role": "assistant", "content": stored_assistant_text},
                ]
            )
            if len(history) > self._conversation_history_messages:
                history = history[-self._conversation_history_messages :]
            histories[key] = history
            return data

        self._state_store.update(updater)

    def _update_agent_session(
        self,
        message: IncomingMessage,
        *,
        active_goal: str,
        plan_intent: str,
        plan_notes: str,
        tool_names: list[str],
        assistant_text: str,
    ) -> None:
        compact_reply = self._compact_history_text(assistant_text)

        def updater(data: dict[str, Any]) -> dict[str, Any]:
            sessions = data.setdefault("agent_sessions", {})
            key = self._agent_session_key(message)
            session = sessions.get(key, {})
            working_memory = list(session.get("working_memory", []))
            working_memory.append(
                {
                    "user": message.text,
                    "intent": plan_intent,
                    "tools": tool_names,
                    "assistant": compact_reply,
                }
            )
            if len(working_memory) > 6:
                working_memory = working_memory[-6:]

            sessions[key] = {
                "active_goal": active_goal,
                "last_intent": plan_intent,
                "last_plan_notes": plan_notes,
                "recent_tool_names": tool_names[-5:],
                "working_memory": working_memory,
            }
            return data

        self._state_store.update(updater)

    def _derive_active_goal(self, latest_user_text: str, session_context: dict[str, Any]) -> str:
        previous_goal = str(session_context.get("active_goal", "")).strip()
        text = latest_user_text.strip()
        if not text:
            return previous_goal
        if self._looks_like_followup(text) and previous_goal:
            return previous_goal
        return text

    def _looks_like_followup(self, text: str) -> bool:
        lowered = text.lower().strip()
        followup_markers = [
            "继续",
            "然后",
            "再",
            "这个",
            "那个",
            "它",
            "这些",
            "那些",
            "进一步",
            "细一点",
            "展开",
            "接着",
            "follow up",
            "continue",
            "and then",
            "what about",
            "that one",
            "those",
            "refine",
            "narrow it",
        ]
        if len(lowered) <= 14:
            return True
        return any(marker in lowered for marker in followup_markers)

    def _compact_history_text(self, assistant_text: str) -> str:
        text = assistant_text.strip()
        if "Web search results for:" in text:
            return self._compact_web_search_history(text)
        if len(text) <= 1200:
            return text
        return text[:1200].rstrip() + "..."

    def _compact_web_search_history(self, text: str) -> str:
        lines = [line.rstrip() for line in text.splitlines() if line.strip()]
        query = ""
        titles: list[str] = []
        for line in lines:
            if line.startswith("Web search results for:"):
                query = line.split(":", maxsplit=1)[1].strip()
                continue
            if line and line[0].isdigit() and ". " in line:
                title = line.split(". ", maxsplit=1)[1].strip()
                titles.append(title)
                if len(titles) >= 3:
                    break

        compact_lines = []
        if query:
            compact_lines.append(f"Ran web_search for: {query}")
        if titles:
            compact_lines.append("Top results seen:")
            compact_lines.extend(f"- {title}" for title in titles)
        if not compact_lines:
            compact_lines.append("Ran web_search and returned external results to the user.")
        compact_lines.append(
            "Search results were shown to the user directly and should be refined, not repeated verbatim."
        )
        return "\n".join(compact_lines)

    def _sanitize_executed_steps_for_model(
        self, executed: list[dict[str, Any]]
    ) -> list[dict[str, Any]] | None:
        sanitized: list[dict[str, Any]] = []
        for step in executed:
            spec = self._registry.get(step["tool_name"])
            if not spec.include_result_in_model_context:
                return None
            sanitized.append(step)
        return sanitized

    def _render_local_execution_summary(
        self,
        intent: str,
        plan_notes: str,
        executed: list[dict[str, Any]],
    ) -> str:
        lines: list[str] = []
        if plan_notes:
            lines.append(plan_notes)
        elif intent:
            lines.append(f"Executed plan: {intent}")

        for step in executed:
            lines.extend(self._render_local_step(step))

        return "\n".join(line for line in lines if line).strip() or "plan executed successfully"

    def _render_local_step(self, step: dict[str, Any]) -> list[str]:
        tool_name = step.get("tool_name", "")
        result = step.get("result") or {}
        if tool_name == "web_search":
            return self._render_web_search_step(result)
        rendered = json.dumps(result, ensure_ascii=False, indent=2)
        return [f"[{tool_name}]", rendered]

    def _render_web_search_step(self, result: dict[str, Any]) -> list[str]:
        lines = [f"Web search results for: {result.get('query', '')}"]
        if result.get("allowed_domains"):
            lines.append(
                "Allowed domains: " + ", ".join(str(item) for item in result["allowed_domains"])
            )

        results = result.get("results", [])
        if not results:
            lines.append("No search results matched the configured restrictions.")
            return lines

        for index, item in enumerate(results, start=1):
            title = str(item.get("title", "")).strip() or "(untitled)"
            domain = str(item.get("domain", "")).strip()
            url = str(item.get("url", "")).strip()
            snippet = str(item.get("snippet", "")).strip()
            label = f"{index}. {title}"
            if domain:
                label += f" [{domain}]"
            lines.append(label)
            if snippet:
                lines.append(f"   {snippet}")
            if url:
                lines.append(f"   {url}")

        lines.append(
            "Safety notice: external search results were returned directly and were not fed back into the model."
        )
        return lines

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
