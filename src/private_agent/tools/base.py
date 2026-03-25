from __future__ import annotations

from dataclasses import MISSING, dataclass, fields, is_dataclass
from pathlib import Path
from typing import Any, Literal, get_args, get_origin, get_type_hints

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
    def field_aliases(cls) -> dict[str, str]:
        return {}

    @classmethod
    def model_validate(cls, data: dict[str, Any]) -> "ToolInputModel":
        if not is_dataclass(cls):
            return cls(**data)

        aliases = cls.field_aliases()
        type_hints = get_type_hints(cls)
        normalized: dict[str, Any] = {}
        field_map = {field.name: field for field in fields(cls)}
        allowed_names = set(field_map)

        for key, value in data.items():
            target_key = aliases.get(key, key)
            if target_key in allowed_names:
                annotation = type_hints.get(target_key, field_map[target_key].type)
                normalized[target_key] = _coerce_value(value, annotation)

        return cls(**normalized)

    @classmethod
    def model_json_schema(cls) -> dict[str, Any]:
        if not is_dataclass(cls):
            return {"type": "object", "title": cls.__name__}

        aliases = cls.field_aliases()
        type_hints = get_type_hints(cls)
        reverse_aliases: dict[str, list[str]] = {}
        for alias, target in aliases.items():
            reverse_aliases.setdefault(target, []).append(alias)

        properties: dict[str, Any] = {}
        required: list[str] = []
        for field in fields(cls):
            annotation = type_hints.get(field.name, field.type)
            schema = {"type": _json_schema_type(annotation)}
            aliases_for_field = reverse_aliases.get(field.name, [])
            if aliases_for_field:
                schema["aliases"] = aliases_for_field
            properties[field.name] = schema
            if field.default is MISSING and field.default_factory is MISSING:
                required.append(field.name)

        return {
            "type": "object",
            "title": cls.__name__,
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        }


def _json_schema_type(annotation: Any) -> str:
    origin = get_origin(annotation)
    if origin is None:
        if annotation in {int}:
            return "integer"
        if annotation in {float}:
            return "number"
        if annotation in {bool}:
            return "boolean"
        if annotation in {list, tuple, set}:
            return "array"
        return "string"

    if origin in {list, tuple, set}:
        return "array"
    if origin is dict:
        return "object"
    if origin is Literal:
        literal_args = get_args(annotation)
        if literal_args:
            return _json_schema_type(type(literal_args[0]))
    return "string"


def _coerce_value(value: Any, annotation: Any) -> Any:
    origin = get_origin(annotation)

    if origin is Literal:
        literal_args = get_args(annotation)
        if not literal_args:
            return value
        target_type = type(literal_args[0])
        coerced = _coerce_value(value, target_type)
        if coerced in literal_args:
            return coerced
        return value

    if origin in {list, tuple, set}:
        item_type = get_args(annotation)[0] if get_args(annotation) else Any
        if isinstance(value, str):
            raw_items = [part.strip() for part in value.split(",") if part.strip()]
        elif isinstance(value, (list, tuple, set)):
            raw_items = list(value)
        else:
            return value
        coerced_items = [_coerce_value(item, item_type) for item in raw_items]
        if origin is tuple:
            return tuple(coerced_items)
        if origin is set:
            return set(coerced_items)
        return coerced_items

    if origin is dict:
        return value

    union_args = get_args(annotation)
    if union_args and type(None) in union_args:
        if value in {None, ""}:
            return None
        non_none = [item for item in union_args if item is not type(None)]
        if len(non_none) == 1:
            return _coerce_value(value, non_none[0])

    if annotation is bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {"1", "true", "yes", "on"}:
                return True
            if lowered in {"0", "false", "no", "off"}:
                return False
        return bool(value)

    if annotation is int:
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        if isinstance(value, str):
            stripped = value.strip()
            if stripped:
                return int(stripped)
        return value

    if annotation is float:
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            stripped = value.strip()
            if stripped:
                return float(stripped)
        return value

    if annotation is str:
        if isinstance(value, str):
            return value
        return str(value)

    return value


@dataclass(slots=True)
class ToolContext:
    allowed_roots: tuple[Path, ...]
    allowed_repos: dict[str, Path]
    notes_dir: Path
    safe_mode: bool
    enable_network_tools: bool = False
    enable_desktop_tools: bool = False
    enable_web_search: bool = False
    web_search_allowed_domains: tuple[str, ...] = ()
    web_search_max_results: int = 5
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
    include_result_in_model_context: bool = True

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
