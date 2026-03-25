from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class AuditEvent:
    trace_id: str
    module: str
    action: str
    result: str
    risk_level: str
    duration_ms: int
    details: dict[str, Any]
    timestamp: str | None = None

    def to_json(self) -> str:
        payload = asdict(self)
        payload["timestamp"] = payload["timestamp"] or datetime.now(timezone.utc).isoformat()
        return json.dumps(payload, ensure_ascii=True)


class AuditLogger:
    def __init__(self, path: Path) -> None:
        self._path = path

    def write(self, event: AuditEvent) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(event.to_json())
            handle.write("\n")
