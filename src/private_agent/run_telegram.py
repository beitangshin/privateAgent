from __future__ import annotations

import asyncio
import json

from private_agent.app import build_app
from private_agent.config import load_settings
from private_agent.sync import InventorySyncServer, InventorySyncStore, MultiPeerInventorySyncStore
from private_agent.transport.commands import HELP_TEXT, parse_command
from private_agent.transport.telegram import TelegramBotClient


def _format_inventory_snapshot(data: dict[str, object]) -> str | None:
    if "available" not in data or "storage_count" not in data or "matches" not in data:
        return None

    if not data.get("available", False):
        return str(data.get("message", "No synced inventory snapshot is available yet."))

    query = str(data.get("query", "")).strip()
    matches = data.get("matches", [])
    if not isinstance(matches, list):
        matches = []

    if query:
        if not matches:
            return f"没有找到“{query}”。"
        lines = [f"找到了 {len(matches)} 条“{query}”相关库存："]
    else:
        storage_count = int(data.get("storage_count", 0))
        if not matches:
            return f"当前库存里有 {storage_count} 个存储区，但没有可展示的条目。"
        lines = [f"当前库存里有 {storage_count} 个存储区，先给你看前 {len(matches)} 条："]

    for entry in matches:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name", "")).strip() or "未命名物品"
        quantity = entry.get("quantity", "")
        unit = str(entry.get("unit", "")).strip()
        storage = str(entry.get("storage", "")).strip()
        box = str(entry.get("box", "")).strip()
        category = str(entry.get("category", "")).strip()
        note = str(entry.get("note", "")).strip()

        detail = f"{name}: {quantity}{unit}"
        location = " / ".join(part for part in (storage, box) if part)
        if location:
            detail += f"，位置 {location}"
        if category and category != "None":
            detail += f"，分类 {category}"
        if note and note != "None":
            detail += f"，备注 {note}"
        lines.append(detail)

    return "\n".join(lines)


def _format_result_message(result: object) -> str:
    if hasattr(result, "status") and hasattr(result, "message"):
        status = str(getattr(result, "status"))
        message = str(getattr(result, "message"))
        data = getattr(result, "data", None)

        if status == "ok":
            if message not in {
                "tool execution succeeded",
                "confirmed tool execution succeeded",
                "plan executed successfully",
            }:
                return message
            if data is not None:
                if isinstance(data, dict):
                    inventory_message = _format_inventory_snapshot(data)
                    if inventory_message is not None:
                        return inventory_message
                return json.dumps(data, ensure_ascii=False, indent=2)
            return message

        if status == "allow_with_confirmation":
            return message

        if status in {"deny", "error", "cancelled"}:
            return message

        if data is not None:
            return f"{message}\n{json.dumps(data, ensure_ascii=False, indent=2)}"
        return message
    return str(result)


async def main() -> None:
    settings = load_settings()
    if not settings.telegram_bot_token:
        raise RuntimeError("PRIVATE_AGENT_TELEGRAM_BOT_TOKEN is required")

    service = build_app()
    sync_server: InventorySyncServer | None = None
    if settings.enable_inventory_sync:
        sync_store = (
            MultiPeerInventorySyncStore(
                root=settings.inventory_sync_dir,
                knowledge_root=settings.knowledge_base_dir,
            )
            if settings.inventory_sync_by_source_ip
            else InventorySyncStore(
                root=settings.inventory_sync_dir,
                knowledge_root=settings.knowledge_base_dir,
            )
        )
        sync_server = InventorySyncServer(
            host=settings.inventory_sync_bind_host,
            port=settings.inventory_sync_port,
            token=settings.inventory_sync_token,
            store=sync_store,
        )
        sync_server.start()
    client = TelegramBotClient(
        settings.telegram_bot_token,
        poll_timeout_sec=settings.telegram_poll_timeout_sec,
    )

    try:
        offset: int | None = None
        while True:
            updates = await client.get_updates(offset=offset)
            for update in updates:
                offset = update.update_id + 1
                try:
                    parsed = parse_command(update.message.text)
                    if parsed.kind == "tool" and parsed.tool_name:
                        result = await service.handle_tool_request(
                            update.message,
                            parsed.tool_name,
                            parsed.args or {},
                        )
                        await client.send_message(update.message.chat_id, _format_result_message(result))
                    elif parsed.kind == "repo_select":
                        result = service.set_active_repo(update.message, (parsed.args or {}).get("repo_name", ""))
                        await client.send_message(update.message.chat_id, _format_result_message(result))
                    elif parsed.kind == "repo_tool" and parsed.tool_name:
                        result = await service.handle_active_repo_tool_request(
                            update.message,
                            parsed.tool_name,
                            parsed.args or {},
                        )
                        await client.send_message(update.message.chat_id, _format_result_message(result))
                    elif parsed.kind == "knowledge_search":
                        result = service.search_knowledge(update.message, (parsed.args or {}).get("query", ""))
                        await client.send_message(update.message.chat_id, _format_result_message(result))
                    elif parsed.kind == "knowledge_add":
                        args = parsed.args or {}
                        result = service.add_knowledge(
                            update.message,
                            args.get("path", ""),
                            args.get("content", ""),
                        )
                        await client.send_message(update.message.chat_id, _format_result_message(result))
                    elif parsed.kind == "inventory_create_storage":
                        args = parsed.args or {}
                        result = service.create_inventory_storage(update.message, args.get("storage_name", ""))
                        await client.send_message(update.message.chat_id, _format_result_message(result))
                    elif parsed.kind == "inventory_create_box":
                        args = parsed.args or {}
                        result = service.create_inventory_box(
                            update.message,
                            args.get("storage_name", ""),
                            args.get("box_name", ""),
                        )
                        await client.send_message(update.message.chat_id, _format_result_message(result))
                    elif parsed.kind == "inventory_set_item":
                        args = parsed.args or {}
                        result = service.upsert_inventory_item(
                            update.message,
                            storage_name=str(args.get("storage_name", "")),
                            box_name=str(args.get("box_name", "")),
                            item_name=str(args.get("item_name", "")),
                            quantity=float(args.get("quantity", 0)),
                            unit=str(args.get("unit", "")),
                            category=args.get("category"),
                            note=args.get("note"),
                        )
                        await client.send_message(update.message.chat_id, _format_result_message(result))
                    elif parsed.kind == "inventory_move_item":
                        args = parsed.args or {}
                        result = service.move_inventory_item(
                            update.message,
                            storage_name=str(args.get("storage_name", "")),
                            item_name=str(args.get("item_name", "")),
                            target_box_name=str(args.get("target_box_name", "")),
                        )
                        await client.send_message(update.message.chat_id, _format_result_message(result))
                    elif parsed.kind == "inventory_delete_item":
                        args = parsed.args or {}
                        result = service.delete_inventory_item(
                            update.message,
                            storage_name=str(args.get("storage_name", "")),
                            item_name=str(args.get("item_name", "")),
                        )
                        await client.send_message(update.message.chat_id, _format_result_message(result))
                    elif parsed.kind == "approve" and parsed.trace_id:
                        result = await service.approve(update.message, parsed.trace_id)
                        await client.send_message(update.message.chat_id, _format_result_message(result))
                    elif parsed.kind == "cancel" and parsed.trace_id:
                        result = service.cancel(update.message, parsed.trace_id)
                        await client.send_message(update.message.chat_id, _format_result_message(result))
                    elif parsed.kind == "list_tools":
                        await client.send_message(
                            update.message.chat_id,
                            json.dumps(service.list_tools(), ensure_ascii=False, indent=2),
                        )
                    elif parsed.kind == "reset_conversation":
                        result = service.reset_conversation(update.message)
                        await client.send_message(update.message.chat_id, _format_result_message(result))
                    elif parsed.kind == "version":
                        result = service.get_version(update.message)
                        await client.send_message(update.message.chat_id, _format_result_message(result))
                    elif not update.message.text.strip().startswith("/"):
                        result = await service.handle_natural_language(update.message)
                        await client.send_message(update.message.chat_id, _format_result_message(result))
                    else:
                        await client.send_message(update.message.chat_id, HELP_TEXT)
                except Exception as exc:  # noqa: BLE001
                    await client.send_message(
                        update.message.chat_id,
                        f"status: error\nmessage: {exc}",
                    )
    finally:
        if sync_server is not None:
            sync_server.stop()


if __name__ == "__main__":
    asyncio.run(main())
