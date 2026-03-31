from __future__ import annotations

import asyncio
import json
from functools import lru_cache
import urllib.error
import urllib.request
from dataclasses import asdict
from pathlib import Path
from typing import Any

from private_agent.audit import ModelCallLogger

from .base import ModelMessage, ModelPlan, ModelPlanStep, ModelSummary


@lru_cache(maxsize=1)
def _load_query_skill_text() -> str:
    skill_path = (
        Path(__file__).resolve().parent
        / "skills"
        / "telegram-query-routing"
        / "SKILL.md"
    )
    return skill_path.read_text(encoding="utf-8").strip()


class DeepSeekCloudBackend:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str,
        prompt_version: str,
        model_call_log_path: Path,
        timeout_sec: int = 30,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._prompt_version = prompt_version
        self._timeout_sec = timeout_sec
        self._model_call_logger = ModelCallLogger(model_call_log_path)

    async def plan(
        self,
        messages: list[ModelMessage],
        tools: list[dict[str, Any]],
        session_context: dict[str, Any] | None = None,
    ) -> ModelPlan:
        payload = {
            "model": self._model,
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
            "messages": self._build_plan_messages(messages, tools, session_context),
        }
        try:
            response = await asyncio.to_thread(self._post_json, "/chat/completions", payload)
            content = self._extract_content(response)
            reasoning_content = self._extract_reasoning_content(response)
            plan = self._parse_plan(content)
            self._model_call_logger.write(
                {
                    "kind": "plan",
                    "model": self._model,
                    "prompt_version": self._prompt_version,
                    "request_messages": [asdict(message) for message in messages],
                    "session_context": session_context,
                    "available_tools": tools,
                    "raw_content": content,
                    "reasoning_content": reasoning_content,
                    "parsed_plan": {
                        "intent": plan.intent,
                        "requires_confirmation": plan.requires_confirmation,
                        "steps": [
                            {"tool_name": step.tool_name, "arguments": step.arguments}
                            for step in plan.steps
                        ],
                        "response_style": plan.response_style,
                        "notes": plan.notes,
                    },
                    "status": "ok",
                }
            )
            return plan
        except Exception as exc:  # noqa: BLE001
            self._model_call_logger.write(
                {
                    "kind": "plan",
                    "model": self._model,
                    "prompt_version": self._prompt_version,
                    "request_messages": [asdict(message) for message in messages],
                    "session_context": session_context,
                    "available_tools": tools,
                    "status": "error",
                    "error": str(exc),
                }
            )
            raise

    async def summarize(
        self, messages: list[ModelMessage], context: dict[str, Any]
    ) -> ModelSummary:
        payload = {
            "model": self._model,
            "temperature": 0.2,
            "messages": self._build_summary_messages(messages, context),
        }
        try:
            response = await asyncio.to_thread(self._post_json, "/chat/completions", payload)
            content = self._extract_content(response)
            reasoning_content = self._extract_reasoning_content(response)
            summary = ModelSummary(content=content)
            self._model_call_logger.write(
                {
                    "kind": "summarize",
                    "model": self._model,
                    "prompt_version": self._prompt_version,
                    "request_messages": [asdict(message) for message in messages],
                    "context": context,
                    "raw_content": content,
                    "reasoning_content": reasoning_content,
                    "summary": summary.content,
                    "status": "ok",
                }
            )
            return summary
        except Exception as exc:  # noqa: BLE001
            self._model_call_logger.write(
                {
                    "kind": "summarize",
                    "model": self._model,
                    "prompt_version": self._prompt_version,
                    "request_messages": [asdict(message) for message in messages],
                    "context": context,
                    "status": "error",
                    "error": str(exc),
                }
            )
            raise

    def _build_plan_messages(
        self,
        messages: list[ModelMessage],
        tools: list[dict[str, Any]],
        session_context: dict[str, Any] | None,
    ) -> list[dict[str, str]]:
        tool_payload = json.dumps(tools, ensure_ascii=False)
        session_payload = json.dumps(session_context or {}, ensure_ascii=False)
        system_prompt = (
            "You are the planning layer for a local-first remote control assistant. "
            "Behave like a persistent agent, not a stateless chat model. Reuse relevant session "
            "context, continue active goals across follow-up turns, and refine previous work instead "
            "of starting from scratch when the user is clearly continuing the same task. "
            "Treat session_context.knowledge_snippets as trusted local knowledge-base excerpts that "
            "represent durable memory and prior documentation for this agent. Prefer relevant local "
            "knowledge over guessing, and use those snippets to ground plans before deciding whether "
            "tools are necessary. "
            "Return strict JSON only with keys intent, requires_confirmation, steps, "
            "response_style, and notes. Each step must contain tool_name and arguments. "
            "For query-style requests, use tool-first planning and do not return zero steps when a "
            "read-only tool can answer. "
            f"Prompt version: {self._prompt_version}. "
            f"{self._build_web_search_guidance(tools)} "
            f"Query routing skill: {_load_query_skill_text()} "
            f"Session context: {session_payload}. "
            f"Available tools: {tool_payload}"
        )
        return [{"role": "system", "content": system_prompt}] + [
            asdict(message) for message in messages
        ]

    def _build_web_search_guidance(self, tools: list[dict[str, Any]]) -> str:
        tool_names = {str(tool.get("name", "")) for tool in tools}
        if "web_search" not in tool_names:
            return ""
        return (
            "When planning web_search, prefer 1-3 focused searches over one vague search. "
            "Rewrite the user's request into the local market's search vocabulary instead of "
            "copying the user's wording literally. Prefer concrete listing or primary-source "
            "domains over blogs, guides, aggregators, and SEO pages. For housing or price "
            "queries, extract city, area, budget, transaction type, room count, and property "
            "type; then search using local listing terminology and likely neighborhood names "
            "when the user gives broad phrases like city center or core area. If earlier search "
            "results looked low-quality, refine the query instead of repeating the same broad terms. "
            "Do not ask web_search for a broad topic page when the user wants concrete listings."
        )

    def _build_summary_messages(
        self, messages: list[ModelMessage], context: dict[str, Any]
    ) -> list[dict[str, str]]:
        context_payload = json.dumps(context, ensure_ascii=False)
        system_prompt = (
            "You summarize local tool results for a trusted user. Keep the reply concise, "
            f"accurate, and operationally useful. Prompt version: {self._prompt_version}. "
            f"Structured context: {context_payload}"
        )
        return [{"role": "system", "content": system_prompt}] + [
            asdict(message) for message in messages
        ]

    def _post_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self._base_url}{path}"
        request = urllib.request.Request(
            url=url,
            method="POST",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout_sec) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace").strip()
            raise RuntimeError(
                f"DeepSeek API HTTP {exc.code}: {error_body or exc.reason}"
            ) from exc

    def _extract_content(self, response: dict[str, Any]) -> str:
        try:
            return response["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError, AttributeError) as exc:
            raise RuntimeError("DeepSeek response did not contain message content") from exc

    def _extract_reasoning_content(self, response: dict[str, Any]) -> str | None:
        try:
            reasoning = response["choices"][0]["message"].get("reasoning_content")
        except (KeyError, IndexError, AttributeError):
            return None
        if reasoning is None:
            return None
        return str(reasoning).strip() or None

    def _parse_plan(self, content: str) -> ModelPlan:
        normalized = self._extract_json_text(content)
        try:
            raw = json.loads(normalized)
        except json.JSONDecodeError as exc:
            raise RuntimeError("DeepSeek planning response was not valid JSON") from exc

        steps = [
            ModelPlanStep(
                tool_name=str(step["tool_name"]),
                arguments=dict(step.get("arguments", {})),
            )
            for step in raw.get("steps", [])
        ]
        return ModelPlan(
            intent=str(raw.get("intent", "unknown_intent")),
            requires_confirmation=bool(raw.get("requires_confirmation", False)),
            steps=steps,
            response_style=str(raw.get("response_style", "short_status")),
            notes=str(raw.get("notes", "")),
        )

    def _extract_json_text(self, content: str) -> str:
        stripped = content.strip()
        if stripped.startswith("```"):
            lines = stripped.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            stripped = "\n".join(lines).strip()
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start != -1 and end != -1 and end >= start:
            return stripped[start : end + 1]
        return stripped
