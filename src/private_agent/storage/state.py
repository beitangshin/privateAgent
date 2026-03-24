from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class StateStore:
    def __init__(self, path: Path) -> None:
        self._path = path

    def save(self, data: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(data, ensure_ascii=True, indent=2), encoding="utf-8")

    def load(self) -> dict[str, Any]:
        if not self._path.exists():
            return {}
        return json.loads(self._path.read_text(encoding="utf-8"))

    def update(self, updater: Any) -> dict[str, Any]:
        data = self.load()
        updated = updater(data) or data
        self.save(updated)
        return updated
