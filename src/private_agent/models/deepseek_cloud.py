from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from dataclasses import asdict
from pathlib import Path
from typing import Any

from private_agent.audit import ModelCallLogger

from .base import ModelDecision, ModelMessage, ModelPlan, ModelPlanStep, ModelSummary


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

    async def decide_next_step(
        self,
        messages: list[ModelMessage],
        tools: list[dict[str, Any]],
        session_context: dict[str, Any] | None = None,
        scratchpad: list[dict[str, Any]] | None = None,
    ) -> ModelDecision:
        payload = {
            "model": self._model,
            "temperature": 0.1,
            "messages": self._build_react_messages(messages, tools, session_context, scratchpad),
        }
        try:
            response = await asyncio.to_thread(self._post_json, "/chat/completions", payload)
            content = self._extract_content(response)
            reasoning_content = self._extract_reasoning_content(response)
            decision = self._parse_decision(content)
            self._model_call_logger.write(
                {
                    "kind": "react_decide",
                    "model": self._model,
                    "prompt_version": self._prompt_version,
                    "request_messages": [asdict(message) for message in messages],
                    "session_context": session_context,
                    "scratchpad": scratchpad or [],
                    "available_tools": tools,
                    "raw_content": content,
                    "reasoning_content": reasoning_content,
                    "parsed_decision": {
                        "thought": decision.thought,
                        "action": decision.action,
                        "action_input": decision.action_input,
                        "final_answer": decision.final_answer,
                    },
                    "status": "ok",
                }
            )
            return decision
        except Exception as exc:  # noqa: BLE001
            self._model_call_logger.write(
                {
                    "kind": "react_decide",
                    "model": self._model,
                    "prompt_version": self._prompt_version,
                    "request_messages": [asdict(message) for message in messages],
                    "session_context": session_context,
                    "scratchpad": scratchpad or [],
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

    def _build_react_messages(
        self,
        messages: list[ModelMessage],
        tools: list[dict[str, Any]],
        session_context: dict[str, Any] | None,
        scratchpad: list[dict[str, Any]] | None,
    ) -> list[dict[str, str]]:
        tool_payload = json.dumps(tools, ensure_ascii=False)
        session_payload = json.dumps(session_context or {}, ensure_ascii=False)
        scratchpad_payload = json.dumps(scratchpad or [], ensure_ascii=False)
        tool_names = [str(tool.get("name", "")).strip() for tool in tools if str(tool.get("name", "")).strip()]
        action_names = ", ".join(tool_names)
        system_prompt = (
            "You are an agent that solves tasks using the ReAct pattern. "
            "Behave like a persistent local-first assistant that can continue prior work using session context. "
            "Treat session_context.knowledge_snippets as trusted local knowledge-base excerpts. "
            "Treat session_context.task_frame as the controller's distilled view of the user's likely goal, hidden substeps, "
            "and decision hints. Use it to infer intent, reduce ambiguity, and avoid low-signal exploration. "
            "You must think step by step and decide whether to use a tool.\n\n"
            "When you need a tool, respond strictly in this format:\n\n"
            "Thought: describe your reasoning briefly\n"
            f"Action: one of [{action_names}]\n"
            "Action Input: a JSON object\n\n"
            "When you have enough information, respond strictly in this format:\n\n"
            "Thought: describe your reasoning briefly\n"
            "Final Answer: your final answer to the user\n\n"
            "Rules:\n"
            "1. Do not invent tool results.\n"
            "2. Do not skip tool use when external information is needed.\n"
            "3. Keep Thought concise.\n"
            "4. Only output one Action at a time.\n"
            "5. Action Input must be valid JSON.\n"
            "6. Infer the user's actual goal before acting.\n"
            "7. Prefer high-signal evidence over noisy candidate matches.\n"
            "8. When several candidates exist, choose the best-supported one unless ambiguity is genuinely unresolved.\n"
            "9. Expand hidden substeps mentally instead of asking the user for every intermediate decision.\n"
            f"Prompt version: {self._prompt_version}. "
            f"{self._build_web_search_guidance(tools)} "
            f"Session context: {session_payload}. "
            f"Scratchpad so far: {scratchpad_payload}. "
            f"Available tools: {tool_payload}"
        )
        return [{"role": "system", "content": system_prompt}] + [
            asdict(message) for message in messages
        ]

    def _build_web_search_guidance(self, tools: list[dict[str, Any]]) -> str:
        tool_names = {str(tool.get("name", "")) for tool in tools}
        guidance: list[str] = []
        if "find_paths" in tool_names and "inspect_project" in tool_names:
            guidance.append(
                "When the user wants to run, build, test, debug, or inspect a named local project "
                "or folder, first use find_paths to locate it, then inspect_project on the best "
                "match, and only then consider run_shell_command or another execution tool. "
                "Do not waste turns repeatedly listing broad parent directories when find_paths is available."
            )
        if "project_map" in tool_names and "patch_file" in tool_names:
            guidance.append(
                "When the user wants code or script changes, first inspect the project with project_map "
                "or read_allowed_file, then use patch_file for minimal exact edits, and finally verify "
                "with run_shell_command when validation is possible. Do not patch blindly before reading "
                "the relevant file contents."
            )
        if "run_shell_command" in tool_names:
            guidance.append(
                "Use run_shell_command only after you know the concrete target path and likely command. "
                "Prefer inspect_project before shell execution when the user is asking how to run something."
            )
        if "web_search" in tool_names:
            guidance.append(
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
        return " ".join(guidance)


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

    def _parse_decision(self, content: str) -> ModelDecision:
        lines = [line.rstrip() for line in content.strip().splitlines() if line.strip()]
        thought = ""
        action = None
        action_input_text = ""
        final_answer_lines: list[str] = []
        capture_final = False
        for line in lines:
            if line.startswith("Thought:"):
                thought = line.split(":", maxsplit=1)[1].strip()
                capture_final = False
                continue
            if line.startswith("Action:"):
                action = line.split(":", maxsplit=1)[1].strip() or None
                capture_final = False
                continue
            if line.startswith("Action Input:"):
                action_input_text = line.split(":", maxsplit=1)[1].strip()
                capture_final = False
                continue
            if line.startswith("Final Answer:"):
                final_answer_lines.append(line.split(":", maxsplit=1)[1].strip())
                capture_final = True
                continue
            if capture_final:
                final_answer_lines.append(line)

        if final_answer_lines:
            return ModelDecision(
                thought=thought,
                final_answer="\n".join(part for part in final_answer_lines if part).strip(),
            )

        if not action:
            raise RuntimeError("DeepSeek ReAct response did not contain an action or final answer")

        try:
            action_input = json.loads(action_input_text or "{}")
        except json.JSONDecodeError as exc:
            raise RuntimeError("DeepSeek ReAct action input was not valid JSON") from exc
        if not isinstance(action_input, dict):
            raise RuntimeError("DeepSeek ReAct action input must be a JSON object")
        return ModelDecision(
            thought=thought,
            action=action,
            action_input=action_input,
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
