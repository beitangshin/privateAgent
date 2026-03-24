from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

ToolCategory = Literal[
    "info",
    "filesystem_read",
    "filesystem_write",
    "desktop_control",
    "automation",
    "network",
    "system",
    "shell_restricted",
]
RiskLevel = Literal["low", "medium", "high"]


class ToolError(RuntimeError):
    """Tool execution failure."""


class ToolInputModel:
    @classmethod
    def model_validate(cls, data: dict[str, Any]) -> "ToolInputModel":
        return cls(**data)

    @classmethod
    def model_json_schema(cls) -> dict[str, Any]:
        return {"type": "object", "title": cls.__name__}


@dataclass(slots=True)
class ToolContext:
    allowed_roots: tuple[Path, ...]
    notes_dir: Path
    safe_mode: bool
    enable_network_tools: bool = False
    enable_desktop_tools: bool = False
    model_backend_name: str = "mock"


@dataclass(slots=True)
class ToolSpec:
    name: str
    description: str
    category: ToolCategory
    risk_level: RiskLevel
    side_effects: bool
    requires_confirmation: bool
    timeout_sec: int
    input_model: type[ToolInputModel]
    handler: Any

    def schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_model.model_json_schema(),
        }


class ToolRegistry:
    def __init__(self, tools: list[ToolSpec]) -> None:
        self._tools = {tool.name: tool for tool in tools}

    def get(self, name: str) -> ToolSpec:
        if name not in self._tools:
            raise ToolError(f"unknown tool '{name}'")
        return self._tools[name]

    def list_specs(self) -> list[ToolSpec]:
        return list(self._tools.values())
