from __future__ import annotations
import copy
import os
import re
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
from private_agent.models.base import ModelBackend, ModelMessage
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
    _MAX_REACT_TURNS = 6
    _MAX_IDENTICAL_ACTION_REPEATS = 2

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
        self._agent_root = Path.cwd().resolve()

    async def handle_natural_language(self, message: IncomingMessage) -> HandleResult:
        trace_id = uuid.uuid4().hex[:12]
        started = time.perf_counter()
        react_trace: list[dict[str, Any]] = []
        result_state = "handled"
        session_context = self._session_context(message)
        knowledge_context = self._knowledge_context(message, session_context)
        session_context["knowledge_snippets"] = knowledge_context
        session_context["task_frame"] = self._build_task_frame(message, session_context)
        conversation = self._conversation_messages(message)
        try:
            self._authorizer.verify(message.sender_id)
            self._authorizer.verify_chat(message.chat_id)
            direct_result = await self._try_handle_local_filesystem_query(message, session_context)
            if direct_result is not None:
                return direct_result
            tools = [
                self._tool_descriptor(spec)
                for spec in self._registry.list_specs()
                if self._is_tool_enabled(spec.name)
            ]
            return await self._run_react_loop(
                trace_id=trace_id,
                message=message,
                conversation=conversation,
                tools=tools,
                session_context=session_context,
                scratchpad=[],
                active_goal=self._derive_active_goal(message.text, session_context),
                react_trace=react_trace,
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
                        "react_trace": react_trace,
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
                handle_result = HandleResult(
                    trace_id=trace_id,
                    status="ok",
                    message="tool execution succeeded",
                    data=result.data,
                )
                return self._finalize_handle_result_after_possible_self_edit(
                    message,
                    handle_result,
                    [{"tool_name": tool_name, "result": result.data}],
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
        if pending.get("kind") == "react_step":
            result = await self._executor.run(pending["tool_name"], pending["args"])
            self._delete_pending_confirmation(trace_id)
            if not result.ok:
                return HandleResult(trace_id=trace_id, status="error", message=result.error or "error")
            scratchpad = list(pending.get("scratchpad", []))
            observation_for_model = self._observation_for_model(pending["tool_name"], result.data)
            if observation_for_model is None:
                local_message = self._render_local_execution_summary(
                    str(pending.get("active_goal", "")) or "react_task",
                    str(pending.get("thought", "")),
                    [
                        {
                            "tool_name": pending["tool_name"],
                            "arguments": pending["args"],
                            "result": result.data,
                        }
                    ],
                )
                original_message = IncomingMessage(
                    platform=message.platform,
                    sender_id=message.sender_id,
                    chat_id=message.chat_id,
                    message_id=message.message_id,
                    text=str(pending.get("original_text", "")),
                    timestamp=message.timestamp,
                    attachments=[],
                )
                self._append_conversation_exchange(original_message, local_message)
                self._update_agent_session(
                    original_message,
                    active_goal=str(pending.get("active_goal", "")) or str(pending.get("original_text", "")),
                    plan_intent=str(pending.get("active_goal", "")) or "react_task",
                    plan_notes=str(pending.get("thought", "")),
                    tool_names=[pending["tool_name"]],
                    assistant_text=local_message,
                )
                self._delete_pending_confirmation(trace_id)
                handle_result = HandleResult(
                    trace_id=trace_id,
                    status="ok",
                    message=local_message,
                    data={
                        "intent": str(pending.get("active_goal", "")) or "react_task",
                        "steps_executed": 1,
                        "executed_steps": [
                            {
                                "thought": pending.get("thought", ""),
                                "action": pending["tool_name"],
                                "action_input": pending["args"],
                                "observation": {"suppressed_untrusted_result": True},
                            }
                        ],
                    },
                )
                return self._finalize_handle_result_after_possible_self_edit(
                    original_message,
                    handle_result,
                    [{"tool_name": pending["tool_name"], "result": result.data}],
                )
            scratchpad.append(
                {
                    "thought": pending.get("thought", ""),
                    "action": pending["tool_name"],
                    "action_input": pending["args"],
                    "observation": observation_for_model,
                }
            )
            return await self._run_react_loop(
                trace_id=trace_id,
                message=IncomingMessage(
                    platform=message.platform,
                    sender_id=message.sender_id,
                    chat_id=message.chat_id,
                    message_id=message.message_id,
                    text=str(pending.get("original_text", "")),
                    timestamp=message.timestamp,
                    attachments=[],
                ),
                conversation=[ModelMessage(role="user", content=str(pending.get("original_text", "")))],
                tools=[
                    self._tool_descriptor(spec)
                    for spec in self._registry.list_specs()
                    if self._is_tool_enabled(spec.name)
                ],
                session_context=self._session_context(message),
                scratchpad=scratchpad,
                active_goal=str(pending.get("active_goal", "")) or str(pending.get("original_text", "")),
                react_trace=[],
            )

        result = await self._executor.run(pending["tool_name"], pending["args"])
        self._delete_pending_confirmation(trace_id)
        if result.ok:
            handle_result = HandleResult(
                trace_id=trace_id,
                status="ok",
                message="confirmed tool execution succeeded",
                data=result.data,
            )
            return self._finalize_handle_result_after_possible_self_edit(
                message,
                handle_result,
                [{"tool_name": pending["tool_name"], "result": result.data}],
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

    def _build_task_frame(
        self, message: IncomingMessage, session_context: dict[str, Any]
    ) -> dict[str, Any]:
        text = message.text.strip()
        active_goal = self._derive_active_goal(text, session_context)
        explicit_path = self._extract_path_from_text(text)
        named_target = self._extract_named_target(text)
        lowered = text.lower()

        if explicit_path:
            request_kind = "path_specific"
        elif self._looks_like_run_help_request(text):
            request_kind = "execute_or_run"
        elif any(marker in lowered for marker in ["文件夹", "目录", "list ", "under ", "read ", "读取"]):
            request_kind = "filesystem_lookup"
        elif any(marker in lowered for marker in ["搜索", "search", "find ", "查找"]):
            request_kind = "search_or_discovery"
        else:
            request_kind = "general"

        hidden_steps: list[str] = []
        if request_kind == "execute_or_run":
            hidden_steps = [
                "locate the real target path",
                "identify the project or artifact type",
                "infer the best entry point or command",
                "execute only after the target is concrete",
            ]
        elif request_kind in {"filesystem_lookup", "search_or_discovery"}:
            hidden_steps = [
                "identify the most likely target",
                "prefer high-signal candidates over noisy matches",
                "read only the minimum files needed before acting",
            ]

        return {
            "latest_user_text": text,
            "normalized_goal": active_goal,
            "request_kind": request_kind,
            "is_follow_up": self._looks_like_followup(text),
            "explicit_paths": [explicit_path] if explicit_path else [],
            "named_targets": [named_target] if named_target else [],
            "hidden_steps": hidden_steps,
            "decision_hints": [
                "infer the user's real goal before choosing a tool",
                "compress ambiguity into one concrete next objective",
                "prefer high-signal tools over broad exploration",
                "ignore noisy low-value matches when better evidence exists",
                "only ask the user to choose when top candidates remain genuinely ambiguous",
            ],
        }

    async def _try_handle_local_filesystem_query(
        self,
        message: IncomingMessage,
        session_context: dict[str, Any],
    ) -> HandleResult | None:
        text = message.text.strip()
        if not text:
            return None
        if text.startswith("/"):
            return None

        explicit_path = self._extract_path_from_text(text)
        if explicit_path and not self._looks_like_edit_request(text):
            return await self._handle_explicit_local_path(message, explicit_path, session_context)

        target_name = self._extract_named_target(text)
        if not target_name:
            return None

        lowered = text.lower()
        wants_listing = any(
            marker in lowered for marker in ["文件夹下", "目录下", "有哪些文件", "what files", "list files", "under "]
        )
        wants_run_help = any(marker in text for marker in ["怎么跑", "如何跑", "运行", "跑完整", "完整的"]) or any(
            marker in lowered for marker in ["how to run", "run ", "execute "]
        )
        if not wants_listing and not wants_run_help:
            return None

        matches = self._find_named_paths(target_name, prefer_directories=True)
        if not matches:
            return HandleResult(
                trace_id=uuid.uuid4().hex[:12],
                status="ok",
                message=f"没有找到名为 `{target_name}` 的目录。",
            )
        if len(matches) > 1:
            chosen_match = self._choose_best_named_path(matches, wants_run_help=wants_run_help)
            if chosen_match is not None:
                return await self._handle_explicit_local_path(message, chosen_match, session_context)
            lines = [f"找到了多个名为 `{target_name}` 的目录，请指定完整路径："]
            lines.extend(f"- {path}" for path in matches[:5])
            return HandleResult(
                trace_id=uuid.uuid4().hex[:12],
                status="ok",
                message="\n".join(lines),
                data={"matches": matches[:5]},
            )
        return await self._handle_explicit_local_path(message, matches[0], session_context)

    async def _handle_explicit_local_path(
        self,
        message: IncomingMessage,
        path_text: str,
        session_context: dict[str, Any],
    ) -> HandleResult:
        trace_id = uuid.uuid4().hex[:12]
        candidate = Path(path_text).expanduser()
        preferred_tool = "list_allowed_directory"
        if candidate.exists() and candidate.is_file():
            preferred_tool = "read_allowed_file"
        elif self._looks_like_run_help_request(message.text):
            preferred_tool = "inspect_project"
        elif self._looks_like_file_request(message.text):
            preferred_tool = "read_allowed_file"

        result = await self._executor.run(preferred_tool, {"path": str(candidate)})
        if result.ok:
            tool_result = {
                "tool_name": preferred_tool,
                "arguments": {"path": str(candidate)},
                "result": result.data,
            }
            rendered = self._render_local_execution_summary(
                str(session_context.get("active_goal", "")).strip() or message.text.strip(),
                "Handled as a direct local filesystem request.",
                [tool_result],
            )
            self._append_conversation_exchange(message, rendered)
            self._update_agent_session(
                message,
                active_goal=self._derive_active_goal(message.text, session_context),
                plan_intent="direct_local_filesystem_request",
                plan_notes="handled without model reasoning",
                tool_names=[preferred_tool],
                assistant_text=rendered,
            )
            return HandleResult(
                trace_id=trace_id,
                status="ok",
                message=rendered,
                data={"intent": "direct_local_filesystem_request", "steps_executed": 1},
            )
        return HandleResult(trace_id=trace_id, status="error", message=result.error or "error")

    def _extract_path_from_text(self, text: str) -> str | None:
        path_match = re.search(r"(/[\w.\-~/]+(?:/[\w.\-]+)*)", text)
        if path_match:
            return path_match.group(1)
        return None

    def _extract_named_target(self, text: str) -> str | None:
        patterns = [
            r"([A-Za-z0-9._-]+)\s*文件夹",
            r"([A-Za-z0-9._-]+)\s*目录",
            r"(?:运行|跑|执行|完整的)\s*([A-Za-z0-9._-]+)",
            r"find\s+files?\s+under\s+([A-Za-z0-9._-]+)",
            r"(?:run|execute)\s+([A-Za-z0-9._-]+)",
            r"under\s+([A-Za-z0-9._-]+)",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                return match.group(1)
        return None

    def _find_named_paths(self, target_name: str, *, prefer_directories: bool) -> list[str]:
        roots = list(getattr(self._executor, "_context").allowed_roots)
        max_depth = 6
        max_matches = 5
        matches: list[str] = []
        target_name_lower = target_name.lower()
        for root in roots:
            broad_search = root.resolve() == Path("/").resolve()
            for current_root, dirs, files in os.walk(root):
                current_root_path = Path(current_root)
                dirs[:] = [
                    entry
                    for entry in dirs
                    if not self._should_skip_search_path(
                        current_root_path / entry, broad_search=broad_search
                    )
                ]
                depth = len(Path(current_root).relative_to(root).parts)
                if depth >= max_depth:
                    dirs[:] = []
                candidates = dirs if prefer_directories else files
                for candidate_name in candidates:
                    if candidate_name.lower() == target_name_lower:
                        candidate_path = current_root_path / candidate_name
                        if self._should_skip_search_path(candidate_path, broad_search=broad_search):
                            continue
                        matches.append(str(candidate_path))
        ranked = sorted(set(matches), key=self._path_search_priority)
        return ranked[:max_matches]

    def _choose_best_named_path(self, matches: list[str], *, wants_run_help: bool) -> str | None:
        if len(matches) <= 1:
            return matches[0] if matches else None

        scored: list[tuple[int, str]] = []
        for path_text in matches:
            score = self._candidate_path_score(path_text, wants_run_help=wants_run_help)
            scored.append((score, path_text))

        scored.sort(key=lambda item: (-item[0], self._path_search_priority(item[1])))
        best_score, best_path = scored[0]
        second_score = scored[1][0] if len(scored) > 1 else None

        if best_score <= 0:
            return None
        if second_score is not None and best_score - second_score < 3:
            return None
        return best_path

    def _candidate_path_score(self, path_text: str, *, wants_run_help: bool) -> int:
        path = Path(path_text)
        score = 0
        priority_bucket, depth, _ = self._path_search_priority(path_text)
        score += max(0, 8 - (priority_bucket * 2))
        score += max(0, 6 - depth)

        if not path.exists() or not path.is_dir():
            return score

        entries = {item.name for item in path.iterdir()}
        project_markers = {
            "pyproject.toml",
            "requirements.txt",
            "setup.py",
            "package.json",
            "Cargo.toml",
            "go.mod",
            "pom.xml",
            "build.gradle",
            "build.gradle.kts",
            "gradlew",
            "Makefile",
            "README.md",
            "README.txt",
        }
        score += sum(4 for marker in project_markers if marker in entries)

        if wants_run_help:
            executable_suffixes = {".sh", ".py", ".pl", ".rb"}
            for item in path.iterdir():
                if item.is_file() and item.suffix.lower() in executable_suffixes:
                    score += 2
                if item.is_file() and item.name.lower().startswith(("run", "start", "launch", "test")):
                    score += 3
                if item.is_dir() and item.name in {"src", "scripts", "tests"}:
                    score += 1
        return score

    def _should_skip_search_path(self, path: Path, *, broad_search: bool) -> bool:
        parts = path.parts
        if "__pycache__" in parts:
            return True
        if broad_search and self._is_path_under(path, Path("/tmp")):
            for part in parts:
                lowered = part.lower()
                if lowered.startswith("pytest-of-") or lowered.startswith("pytest-"):
                    return True
        return False

    def _path_search_priority(self, path_text: str) -> tuple[int, int, str]:
        path = Path(path_text)
        path_string = str(path)
        home = Path(os.path.expanduser("~")).resolve()
        cwd = Path.cwd().resolve()

        if self._is_path_under(path, home):
            bucket = 0
        elif self._is_path_under(path, cwd.parent):
            bucket = 1
        elif self._is_path_under(path, cwd):
            bucket = 2
        elif self._is_path_under(path, Path("/tmp")):
            bucket = 4
        else:
            bucket = 3
        return (bucket, len(path.parts), path_string.lower())

    def _is_path_under(self, path: Path, root: Path) -> bool:
        try:
            path.resolve().relative_to(root.resolve())
            return True
        except ValueError:
            return False

    def _looks_like_file_request(self, text: str) -> bool:
        lowered = text.lower()
        return any(marker in lowered for marker in ["read ", "读取", "内容", "open ", "查看文件"])

    def _looks_like_edit_request(self, text: str) -> bool:
        lowered = text.lower()
        return any(
            marker in lowered
            for marker in [
                "修改",
                "改一下",
                "改成",
                "编辑",
                "patch",
                "replace",
                "update ",
                "change ",
                "fix ",
                "rewrite ",
            ]
        )

    def _looks_like_run_help_request(self, text: str) -> bool:
        lowered = text.lower()
        return any(marker in text for marker in ["怎么跑", "如何跑", "运行", "跑完整", "完整的"]) or any(
            marker in lowered for marker in ["how to run", "run ", "execute ", "start ", "launch "]
        )

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
        if tool_name == "inspect_project":
            return self._render_inspect_project_step(result)
        if tool_name == "project_map":
            return self._render_project_map_step(result)
        if tool_name == "patch_file":
            return self._render_patch_file_step(result)
        rendered = json.dumps(result, ensure_ascii=False, indent=2)
        return [f"[{tool_name}]", rendered]

    def _render_inspect_project_step(self, result: dict[str, Any]) -> list[str]:
        lines = ["[inspect_project]"]
        path = str(result.get("path", "")).strip()
        if path:
            lines.append(f"Path: {path}")

        detected_types = result.get("detected_types") or []
        if detected_types:
            lines.append("Detected types: " + ", ".join(str(item) for item in detected_types))
        else:
            lines.append("Detected types: no strong project markers found yet")

        likely_entrypoints = result.get("likely_entrypoints") or []
        if likely_entrypoints:
            lines.append(f"Best guess entrypoint: {likely_entrypoints[0]}")
            if len(likely_entrypoints) > 1:
                lines.append("Other entrypoints:")
                lines.extend(f"- {item}" for item in likely_entrypoints[1:5])

        suggested_commands = result.get("suggested_commands") or []
        if suggested_commands:
            lines.append(f"Best guess command: {suggested_commands[0]}")
            if len(suggested_commands) > 1:
                lines.append("Other commands:")
                lines.extend(f"- {item}" for item in suggested_commands[1:5])

        readme_path = str(result.get("readme_path", "") or "").strip()
        if readme_path:
            lines.append(f"README: {readme_path}")

        entry_names = result.get("entry_names") or []
        if entry_names:
            lines.append("Top-level entries: " + ", ".join(str(item) for item in entry_names[:12]))

        return lines

    def _render_project_map_step(self, result: dict[str, Any]) -> list[str]:
        lines = ["[project_map]"]
        path = str(result.get("path", "")).strip()
        if path:
            lines.append(f"Path: {path}")
        detected_types = result.get("detected_types") or []
        if detected_types:
            lines.append("Detected types: " + ", ".join(str(item) for item in detected_types))
        likely_entrypoints = result.get("likely_entrypoints") or []
        if likely_entrypoints:
            lines.append(f"Best guess entrypoint: {likely_entrypoints[0]}")
        tree = result.get("tree") or []
        if tree:
            lines.append("Project tree:")
            lines.extend(f"- {line}" for line in tree[:12])
        suggested_commands = result.get("suggested_commands") or []
        if suggested_commands:
            lines.append("Suggested commands:")
            lines.extend(f"- {item}" for item in suggested_commands[:4])
        return lines

    def _render_patch_file_step(self, result: dict[str, Any]) -> list[str]:
        path = str(result.get("path", "")).strip()
        replacements = result.get("replacements")
        bytes_written = result.get("bytes_written")
        lines = ["[patch_file]"]
        if path:
            lines.append(f"Updated: {path}")
        if replacements is not None:
            lines.append(f"Replacements: {replacements}")
        if bytes_written is not None:
            lines.append(f"Bytes written: {bytes_written}")
        return lines

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

    async def _run_react_loop(
        self,
        *,
        trace_id: str,
        message: IncomingMessage,
        conversation: list[ModelMessage],
        tools: list[dict[str, Any]],
        session_context: dict[str, Any],
        scratchpad: list[dict[str, Any]],
        active_goal: str,
        react_trace: list[dict[str, Any]],
    ) -> HandleResult:
        executed_tool_names = [
            str(step.get("action", "")).strip() for step in scratchpad if step.get("action")
        ]
        for _ in range(self._MAX_REACT_TURNS):
            decision = await self._model_backend.decide_next_step(
                conversation,
                tools,
                session_context=session_context,
                scratchpad=copy.deepcopy(scratchpad),
            )
            react_trace.append(
                {
                    "thought": decision.thought,
                    "action": decision.action,
                    "action_input": decision.action_input,
                    "final_answer": decision.final_answer,
                }
            )
            if decision.final_answer:
                final_message = decision.final_answer.strip()
                handle_result = HandleResult(
                    trace_id=trace_id,
                    status="ok",
                    message=final_message,
                    data={
                        "intent": active_goal or "react_task",
                        "steps_executed": len(executed_tool_names),
                        "executed_steps": scratchpad,
                    },
                )
                if self._scratchpad_modified_self_agent(scratchpad):
                    return self._finalize_handle_result_after_possible_self_edit(
                        message,
                        handle_result,
                        [
                            {
                                "tool_name": str(step.get("action", "")),
                                "result": step.get("observation"),
                            }
                            for step in scratchpad
                        ],
                    )
                self._append_conversation_exchange(message, final_message)
                self._update_agent_session(
                    message,
                    active_goal=active_goal,
                    plan_intent=active_goal or "react_task",
                    plan_notes=decision.thought,
                    tool_names=executed_tool_names,
                    assistant_text=final_message,
                )
                return handle_result

            if not decision.action:
                raise RuntimeError("model did not provide an action or final answer")

            if self._has_repeated_action_loop(scratchpad, decision.action, decision.action_input):
                loop_message = (
                    f"agent stopped after repeating the same action '{decision.action}' "
                    "without reaching a final answer"
                )
                self._append_conversation_exchange(message, loop_message)
                self._update_agent_session(
                    message,
                    active_goal=active_goal,
                    plan_intent=active_goal or "react_task",
                    plan_notes="react loop stopped due to repeated identical action",
                    tool_names=executed_tool_names,
                    assistant_text=loop_message,
                )
                return HandleResult(
                    trace_id=trace_id,
                    status="error",
                    message=loop_message,
                    data={
                        "intent": active_goal or "react_task",
                        "steps_executed": len(executed_tool_names),
                        "executed_steps": scratchpad,
                    },
                )

            spec = self._registry.get(decision.action)
            policy_decision = self._policy.evaluate(spec)
            if policy_decision.state == "deny":
                return HandleResult(trace_id=trace_id, status="deny", message=policy_decision.reason)
            if policy_decision.state == "allow_with_confirmation":
                self._save_pending_confirmation(
                    trace_id=trace_id,
                    sender_id=message.sender_id,
                    chat_id=message.chat_id,
                    payload={
                        "kind": "react_step",
                        "original_text": message.text,
                        "active_goal": active_goal,
                        "thought": decision.thought,
                        "tool_name": decision.action,
                        "args": decision.action_input,
                        "scratchpad": scratchpad,
                    },
                )
                return HandleResult(
                    trace_id=trace_id,
                    status="allow_with_confirmation",
                    message=(
                        f"tool '{decision.action}' needs confirmation. "
                        f"Reply with CONFIRM {trace_id}"
                    ),
                    data={"intent": active_goal or "react_task", "pending_action": decision.action},
                )

            result = await self._executor.run(decision.action, decision.action_input)
            if not result.ok:
                scratchpad.append(
                    {
                        "thought": decision.thought,
                        "action": decision.action,
                        "action_input": decision.action_input,
                        "observation": {"error": result.error or "tool execution failed"},
                    }
                )
                executed_tool_names.append(decision.action)
                continue

            step_record = {
                "thought": decision.thought,
                "action": decision.action,
                "action_input": decision.action_input,
                "observation": result.data,
            }
            scratchpad.append(step_record)
            executed_tool_names.append(decision.action)

            observation_for_model = self._observation_for_model(decision.action, result.data)
            if observation_for_model is None:
                local_message = self._render_local_execution_summary(
                    active_goal or "react_task",
                    decision.thought,
                    [
                        {
                            "tool_name": decision.action,
                            "arguments": decision.action_input,
                            "result": result.data,
                        }
                    ],
                )
                self._append_conversation_exchange(message, local_message)
                self._update_agent_session(
                    message,
                    active_goal=active_goal,
                    plan_intent=active_goal or "react_task",
                    plan_notes=decision.thought,
                    tool_names=executed_tool_names,
                    assistant_text=local_message,
                )
                handle_result = HandleResult(
                    trace_id=trace_id,
                    status="ok",
                    message=local_message,
                    data={
                        "intent": active_goal or "react_task",
                        "steps_executed": len(executed_tool_names),
                        "executed_steps": scratchpad,
                    },
                )
                return self._finalize_handle_result_after_possible_self_edit(
                    message,
                    handle_result,
                    [{"tool_name": decision.action, "result": result.data}],
                )

            scratchpad[-1]["observation"] = observation_for_model

        fallback = "I could not finish the task safely within the step limit."
        self._append_conversation_exchange(message, fallback)
        self._update_agent_session(
            message,
            active_goal=active_goal,
            plan_intent=active_goal or "react_task",
            plan_notes="react loop exceeded max turns",
            tool_names=executed_tool_names,
            assistant_text=fallback,
        )
        return HandleResult(
            trace_id=trace_id,
            status="error",
            message=fallback,
            data={
                "intent": active_goal or "react_task",
                "steps_executed": len(executed_tool_names),
                "executed_steps": scratchpad,
            },
        )

    def _observation_for_model(self, tool_name: str, result: dict[str, Any] | None) -> dict[str, Any] | None:
        spec = self._registry.get(tool_name)
        if not spec.include_result_in_model_context:
            return None
        return result or {}

    def _has_repeated_action_loop(
        self,
        scratchpad: list[dict[str, Any]],
        action: str,
        action_input: dict[str, Any],
    ) -> bool:
        recent_actions = [
            (
                str(step.get("action", "")).strip(),
                json.dumps(step.get("action_input", {}), ensure_ascii=False, sort_keys=True),
            )
            for step in scratchpad[-self._MAX_IDENTICAL_ACTION_REPEATS :]
            if step.get("action")
        ]
        if len(recent_actions) < self._MAX_IDENTICAL_ACTION_REPEATS:
            return False
        current = (action.strip(), json.dumps(action_input, ensure_ascii=False, sort_keys=True))
        return all(item == current for item in recent_actions)

    def _scratchpad_modified_self_agent(self, scratchpad: list[dict[str, Any]]) -> bool:
        for step in scratchpad:
            if self._tool_result_modified_self_agent(
                str(step.get("action", "")),
                step.get("observation"),
            ):
                return True
        return False

    def _tool_result_modified_self_agent(self, tool_name: str, result: dict[str, Any] | None) -> bool:
        if tool_name != "patch_file" or not isinstance(result, dict):
            return False
        path_text = str(result.get("path", "")).strip()
        if not path_text:
            return False
        candidate = Path(path_text).resolve()
        try:
            candidate.relative_to(self._agent_root)
            return True
        except ValueError:
            return False

    def _clear_conversation_state(self, message: IncomingMessage) -> None:
        def updater(data: dict[str, Any]) -> dict[str, Any]:
            histories = data.setdefault("conversation_histories", {})
            histories.pop(self._conversation_session_key(message), None)
            sessions = data.setdefault("agent_sessions", {})
            sessions.pop(self._agent_session_key(message), None)
            return data

        self._state_store.update(updater)

    def _finalize_handle_result_after_possible_self_edit(
        self,
        message: IncomingMessage,
        result: HandleResult,
        executed_steps: list[dict[str, Any]],
    ) -> HandleResult:
        if not any(
            self._tool_result_modified_self_agent(
                str(step.get("tool_name", "")),
                step.get("result"),
            )
            for step in executed_steps
        ):
            return result
        self._clear_conversation_state(message)
        reset_notice = (
            "Self-agent code changed. Conversation memory was cleared for this chat. "
            "Send the next instruction as a fresh turn."
        )
        combined_message = result.message.strip()
        if combined_message:
            combined_message = f"{combined_message}\n\n{reset_notice}"
        else:
            combined_message = reset_notice
        data = dict(result.data or {})
        data["restart_required"] = True
        data["conversation_reset"] = True
        return HandleResult(
            trace_id=result.trace_id,
            status=result.status,
            message=combined_message,
            data=data,
        )
