from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class ModelCallLogger:
    def __init__(self, path: Path) -> None:
        self._path = path

    def write(self, payload: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **payload,
        }
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False))
            handle.write("\n")
