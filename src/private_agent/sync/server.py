from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def _normalize_text(value: str) -> str:
    return value.strip()


def _normalize_optional(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


@dataclass(slots=True)
class InventorySyncStore:
    root: Path
    knowledge_root: Path

    @property
    def _inventory_file(self) -> Path:
        return self.root / "current_inventory.json"

    @property
    def _change_queue_file(self) -> Path:
        return self.root / "change_queue.json"

    def save_snapshot(
        self,
        payload: dict[str, Any],
        *,
        acknowledged_change_seq: int | None = None,
        source: str = "android",
    ) -> dict[str, Any]:
        normalized = self._normalize_snapshot(payload)
        self.root.mkdir(parents=True, exist_ok=True)
        self._inventory_file.write_text(
            json.dumps(normalized, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        if acknowledged_change_seq is not None:
            self._prune_acknowledged_changes(acknowledged_change_seq)
        self._write_knowledge_artifacts(normalized)

        storages = normalized.get("storages", [])
        total_boxes = sum(
            len(storage.get("boxes", [])) for storage in storages if isinstance(storage, dict)
        )
        total_items = sum(
            len(box.get("items", []))
            for storage in storages
            if isinstance(storage, dict)
            for box in storage.get("boxes", [])
            if isinstance(box, dict)
        )
        return {
            "saved_at": _now_iso(),
            "exported_at": normalized.get("exported_at", ""),
            "source": source,
            "storage_count": len(storages),
            "box_count": total_boxes,
            "item_count": total_items,
            "latest_change_seq": self.latest_change_seq(),
            "inventory_file": str(self._inventory_file),
            "knowledge_file": str(self.knowledge_root / "projects" / "fridge-system" / "current_inventory.md"),
        }

    def load_snapshot(self) -> dict[str, Any] | None:
        if not self._inventory_file.exists():
            return None
        return json.loads(self._inventory_file.read_text(encoding="utf-8"))

    def get_changes(self, after_seq: int) -> dict[str, Any]:
        state = self._load_change_state()
        changes = [
            change
            for change in state.get("changes", [])
            if int(change.get("seq", 0)) > after_seq
        ]
        return {
            "changes": changes,
            "latest_change_seq": int(state.get("next_seq", 1)) - 1,
            "snapshot": self.load_snapshot() or self._empty_snapshot(),
        }

    def latest_change_seq(self) -> int:
        state = self._load_change_state()
        return int(state.get("next_seq", 1)) - 1

    def create_storage(self, storage_name: str) -> dict[str, Any]:
        snapshot = self.load_snapshot() or self._empty_snapshot()
        storage_name = _normalize_text(storage_name)
        if not storage_name:
            raise RuntimeError("storage name is required")
        if self._find_storage(snapshot, storage_name) is None:
            snapshot["storages"].append(
                {
                    "name": storage_name,
                    "boxes": [{"name": "Default", "items": []}],
                }
            )
            self._touch_snapshot(snapshot, app_version="privateAgent")
            self.save_snapshot(snapshot, source="telegram")
        change = self._append_change(
            {
                "type": "create_storage",
                "storage_name": storage_name,
            }
        )
        return {"message": f"Storage '{storage_name}' is ready.", "change": change}

    def create_box(self, storage_name: str, box_name: str) -> dict[str, Any]:
        snapshot = self.load_snapshot() or self._empty_snapshot()
        storage = self._ensure_storage(snapshot, storage_name)
        box_name = _normalize_text(box_name)
        if not box_name:
            raise RuntimeError("box name is required")
        if self._find_box(storage, box_name) is None:
            storage["boxes"].append({"name": box_name, "items": []})
            self._touch_snapshot(snapshot, app_version="privateAgent")
            self.save_snapshot(snapshot, source="telegram")
        change = self._append_change(
            {
                "type": "create_box",
                "storage_name": storage["name"],
                "box_name": box_name,
            }
        )
        return {"message": f"Box '{box_name}' is ready in '{storage['name']}'.", "change": change}

    def upsert_item(
        self,
        *,
        storage_name: str,
        box_name: str,
        item_name: str,
        quantity: float,
        unit: str,
        category: str | None,
        note: str | None,
    ) -> dict[str, Any]:
        if quantity <= 0:
            raise RuntimeError("quantity must be greater than 0")
        if not unit.strip():
            raise RuntimeError("unit is required")

        snapshot = self.load_snapshot() or self._empty_snapshot()
        storage = self._ensure_storage(snapshot, storage_name)
        target_box = self._ensure_box(storage, box_name)
        item_name = _normalize_text(item_name)
        if not item_name:
            raise RuntimeError("item name is required")

        existing_item, existing_box = self._find_item(storage, item_name)
        if existing_item is None:
            existing_item = {}
            target_box["items"].append(existing_item)
        elif existing_box is not None and existing_box is not target_box:
            existing_box["items"] = [
                item for item in existing_box.get("items", []) if item is not existing_item
            ]
            target_box["items"].append(existing_item)

        existing_item.update(
            {
                "name": item_name,
                "quantity": quantity,
                "unit": unit.strip(),
                "category": _normalize_optional(category),
                "note": _normalize_optional(note),
                "updated_at": _now_ms(),
            }
        )
        self._touch_snapshot(snapshot, app_version="privateAgent")
        self.save_snapshot(snapshot, source="telegram")
        change = self._append_change(
            {
                "type": "upsert_item",
                "storage_name": storage["name"],
                "box_name": target_box["name"],
                "item_name": item_name,
                "quantity": quantity,
                "unit": unit.strip(),
                "category": _normalize_optional(category),
                "note": _normalize_optional(note),
            }
        )
        return {
            "message": (
                f"Saved '{item_name}' in '{storage['name']} / {target_box['name']}' "
                f"with {quantity} {unit.strip()}."
            ),
            "change": change,
        }

    def move_item(self, *, storage_name: str, item_name: str, target_box_name: str) -> dict[str, Any]:
        snapshot = self.load_snapshot() or self._empty_snapshot()
        storage = self._ensure_storage(snapshot, storage_name)
        item_name = _normalize_text(item_name)
        target_box = self._ensure_box(storage, target_box_name)
        item, current_box = self._find_item(storage, item_name)
        if item is None or current_box is None:
            raise RuntimeError(f"item '{item_name}' was not found in storage '{storage['name']}'")
        if current_box["name"] == target_box["name"]:
            raise RuntimeError("item is already in that box")
        current_box["items"] = [candidate for candidate in current_box.get("items", []) if candidate is not item]
        item["updated_at"] = _now_ms()
        target_box["items"].append(item)
        self._touch_snapshot(snapshot, app_version="privateAgent")
        self.save_snapshot(snapshot, source="telegram")
        change = self._append_change(
            {
                "type": "move_item",
                "storage_name": storage["name"],
                "item_name": item_name,
                "box_name": current_box["name"],
                "target_box_name": target_box["name"],
            }
        )
        return {
            "message": f"Moved '{item_name}' to '{storage['name']} / {target_box['name']}'.",
            "change": change,
        }

    def delete_item(self, *, storage_name: str, item_name: str) -> dict[str, Any]:
        snapshot = self.load_snapshot() or self._empty_snapshot()
        storage = self._ensure_storage(snapshot, storage_name)
        item_name = _normalize_text(item_name)
        item, current_box = self._find_item(storage, item_name)
        if item is None or current_box is None:
            raise RuntimeError(f"item '{item_name}' was not found in storage '{storage['name']}'")
        current_box["items"] = [candidate for candidate in current_box.get("items", []) if candidate is not item]
        self._touch_snapshot(snapshot, app_version="privateAgent")
        self.save_snapshot(snapshot, source="telegram")
        change = self._append_change(
            {
                "type": "delete_item",
                "storage_name": storage["name"],
                "item_name": item_name,
            }
        )
        return {
            "message": f"Deleted '{item_name}' from '{storage['name']}'.",
            "change": change,
        }

    def _load_change_state(self) -> dict[str, Any]:
        if not self._change_queue_file.exists():
            return {"next_seq": 1, "changes": []}
        state = json.loads(self._change_queue_file.read_text(encoding="utf-8"))
        if "next_seq" not in state or "changes" not in state:
            return {"next_seq": 1, "changes": []}
        return state

    def _save_change_state(self, state: dict[str, Any]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self._change_queue_file.write_text(
            json.dumps(state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _append_change(self, payload: dict[str, Any]) -> dict[str, Any]:
        state = self._load_change_state()
        seq = int(state.get("next_seq", 1))
        change = {
            "seq": seq,
            "created_at": _now_iso(),
            "created_at_ms": _now_ms(),
        }
        change.update(payload)
        changes = list(state.get("changes", []))
        changes.append(change)
        self._save_change_state({"next_seq": seq + 1, "changes": changes})
        return change

    def _prune_acknowledged_changes(self, acknowledged_change_seq: int) -> None:
        state = self._load_change_state()
        changes = [
            change
            for change in state.get("changes", [])
            if int(change.get("seq", 0)) > acknowledged_change_seq
        ]
        self._save_change_state({"next_seq": state.get("next_seq", 1), "changes": changes})

    def _empty_snapshot(self) -> dict[str, Any]:
        return {
            "exported_at": _now_iso(),
            "app_version": "privateAgent",
            "storages": [],
        }

    def _normalize_snapshot(self, payload: dict[str, Any]) -> dict[str, Any]:
        snapshot = dict(payload)
        storages: list[dict[str, Any]] = []
        for raw_storage in snapshot.get("storages", []):
            if not isinstance(raw_storage, dict):
                continue
            storage = {
                "name": _normalize_text(str(raw_storage.get("name", ""))),
                "boxes": [],
            }
            if not storage["name"]:
                continue
            for raw_box in raw_storage.get("boxes", []):
                if not isinstance(raw_box, dict):
                    continue
                box = {
                    "name": _normalize_text(str(raw_box.get("name", ""))),
                    "items": [],
                }
                if not box["name"]:
                    continue
                for raw_item in raw_box.get("items", []):
                    if not isinstance(raw_item, dict):
                        continue
                    item_name = _normalize_text(str(raw_item.get("name", "")))
                    unit = _normalize_text(str(raw_item.get("unit", "")))
                    if not item_name or not unit:
                        continue
                    box["items"].append(
                        {
                            "name": item_name,
                            "quantity": float(raw_item.get("quantity", 0)),
                            "unit": unit,
                            "category": _normalize_optional(raw_item.get("category")),
                            "note": _normalize_optional(raw_item.get("note")),
                            "updated_at": int(raw_item.get("updated_at", _now_ms())),
                        }
                    )
                storage["boxes"].append(box)
            storages.append(storage)
        return {
            "exported_at": str(snapshot.get("exported_at", _now_iso())),
            "app_version": str(snapshot.get("app_version", "android-app")),
            "storages": storages,
        }

    def _touch_snapshot(self, snapshot: dict[str, Any], *, app_version: str) -> None:
        snapshot["exported_at"] = _now_iso()
        snapshot["app_version"] = app_version

    def _write_knowledge_artifacts(self, payload: dict[str, Any]) -> None:
        knowledge_dir = self.knowledge_root / "projects" / "fridge-system"
        knowledge_dir.mkdir(parents=True, exist_ok=True)
        (knowledge_dir / "current_inventory.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (knowledge_dir / "current_inventory.md").write_text(
            self._build_markdown_summary(payload),
            encoding="utf-8",
        )

    def _build_markdown_summary(self, payload: dict[str, Any]) -> str:
        storages = payload.get("storages", [])
        lines = [
            "# FridgeSystem Inventory Sync",
            "",
            f"- Exported at: {payload.get('exported_at', '')}",
            f"- App version: {payload.get('app_version', '')}",
            f"- Storage count: {len(storages)}",
            f"- Latest change seq: {self.latest_change_seq()}",
            "",
        ]
        for storage in storages:
            if not isinstance(storage, dict):
                continue
            lines.append(f"## Storage: {storage.get('name', '')}")
            for box in storage.get("boxes", []):
                if not isinstance(box, dict):
                    continue
                lines.append(f"### Box: {box.get('name', '')}")
                for item in box.get("items", []):
                    if not isinstance(item, dict):
                        continue
                    lines.append(
                        f"- Item: {item.get('name', '')} | Quantity: {item.get('quantity', '')} {item.get('unit', '')} | "
                        f"Location: {storage.get('name', '')} / {box.get('name', '')}"
                    )
                lines.append("")
        return "\n".join(lines).strip() + "\n"

    def _find_storage(self, snapshot: dict[str, Any], storage_name: str) -> dict[str, Any] | None:
        normalized = _normalize_text(storage_name)
        for storage in snapshot.get("storages", []):
            if _normalize_text(str(storage.get("name", ""))) == normalized:
                return storage
        return None

    def _ensure_storage(self, snapshot: dict[str, Any], storage_name: str) -> dict[str, Any]:
        normalized = _normalize_text(storage_name)
        if not normalized:
            raise RuntimeError("storage name is required")
        storage = self._find_storage(snapshot, normalized)
        if storage is not None:
            return storage
        storage = {"name": normalized, "boxes": []}
        snapshot["storages"].append(storage)
        return storage

    def _find_box(self, storage: dict[str, Any], box_name: str) -> dict[str, Any] | None:
        normalized = _normalize_text(box_name)
        for box in storage.get("boxes", []):
            if _normalize_text(str(box.get("name", ""))) == normalized:
                return box
        return None

    def _ensure_box(self, storage: dict[str, Any], box_name: str) -> dict[str, Any]:
        normalized = _normalize_text(box_name)
        if not normalized:
            raise RuntimeError("box name is required")
        box = self._find_box(storage, normalized)
        if box is not None:
            return box
        box = {"name": normalized, "items": []}
        storage["boxes"].append(box)
        return box

    def _find_item(
        self, storage: dict[str, Any], item_name: str
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        normalized = _normalize_text(item_name)
        for box in storage.get("boxes", []):
            for item in box.get("items", []):
                if _normalize_text(str(item.get("name", ""))) == normalized:
                    return item, box
        return None, None


class InventorySyncServer:
    def __init__(
        self,
        *,
        host: str,
        port: int,
        token: str | None,
        store: InventorySyncStore,
    ) -> None:
        self._host = host
        self._port = port
        self._token = token
        self._store = store
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._server is not None:
            return

        token = self._token
        store = self._store

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                parsed = urlparse(self.path)
                if parsed.path != "/inventory/sync":
                    self.send_error(HTTPStatus.NOT_FOUND)
                    return
                if not self._authorized(token):
                    return
                query = parse_qs(parsed.query)
                after_seq = int(query.get("after", ["0"])[0] or "0")
                payload = {"ok": True}
                payload.update(store.get_changes(after_seq))
                self._send_json(payload, HTTPStatus.OK)

            def do_POST(self) -> None:  # noqa: N802
                parsed = urlparse(self.path)
                if parsed.path != "/inventory/sync":
                    self.send_error(HTTPStatus.NOT_FOUND)
                    return
                if not self._authorized(token):
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    raw_body = self.rfile.read(length).decode("utf-8")
                    payload = json.loads(raw_body)
                    acknowledged_change_seq = payload.get("acknowledged_change_seq")
                    source = str(payload.get("source", "android"))
                    summary = store.save_snapshot(
                        payload,
                        acknowledged_change_seq=int(acknowledged_change_seq)
                        if acknowledged_change_seq is not None
                        else None,
                        source=source,
                    )
                except Exception as exc:  # noqa: BLE001
                    self._send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)
                    return
                self._send_json({"ok": True, "summary": summary}, HTTPStatus.OK)

            def _authorized(self, expected_token: str | None) -> bool:
                if not expected_token:
                    return True
                auth = self.headers.get("Authorization", "")
                if auth == f"Bearer {expected_token}":
                    return True
                self.send_error(HTTPStatus.UNAUTHORIZED)
                return False

            def _send_json(self, payload: dict[str, Any], status: HTTPStatus) -> None:
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps(payload, ensure_ascii=False).encode("utf-8"))

            def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
                return

        self._server = ThreadingHTTPServer((self._host, self._port), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._server is None:
            return
        self._server.shutdown()
        self._server.server_close()
        self._server = None
        self._thread = None
