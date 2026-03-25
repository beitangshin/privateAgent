from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from private_agent.transport.types import IncomingMessage


@dataclass(slots=True)
class TelegramUpdate:
    update_id: int
    message: IncomingMessage


class TelegramBotClient:
    def __init__(self, token: str, *, poll_timeout_sec: int = 20) -> None:
        self._base_url = f"https://api.telegram.org/bot{token}"
        self._poll_timeout_sec = poll_timeout_sec

    async def get_updates(self, offset: int | None = None) -> list[TelegramUpdate]:
        payload: dict[str, Any] = {"timeout": self._poll_timeout_sec}
        if offset is not None:
            payload["offset"] = offset
        response = await asyncio.to_thread(self._post_json, "getUpdates", payload)
        updates: list[TelegramUpdate] = []
        for item in response.get("result", []):
            raw_message = item.get("message")
            if not raw_message or "text" not in raw_message:
                continue
            updates.append(
                TelegramUpdate(
                    update_id=item["update_id"],
                    message=IncomingMessage(
                        platform="telegram",
                        sender_id=str(raw_message["from"]["id"]),
                        chat_id=str(raw_message["chat"]["id"]),
                        message_id=str(raw_message["message_id"]),
                        text=raw_message["text"],
                        timestamp=datetime.fromtimestamp(raw_message["date"], tz=timezone.utc),
                    ),
                )
            )
        return updates

    async def send_message(self, chat_id: str, text: str) -> None:
        await asyncio.to_thread(
            self._post_json,
            "sendMessage",
            {"chat_id": chat_id, "text": text},
        )

    def _post_json(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        data = urlencode(payload).encode("utf-8")
        request = Request(f"{self._base_url}/{method}", data=data)
        with urlopen(request, timeout=self._poll_timeout_sec + 10) as response:  # noqa: S310
            return json.loads(response.read().decode("utf-8"))
