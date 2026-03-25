from __future__ import annotations

import asyncio
import json

from private_agent.config import load_settings
from private_agent.transport.telegram import TelegramBotClient


async def main() -> None:
    settings = load_settings()
    if not settings.telegram_bot_token:
        raise RuntimeError("PRIVATE_AGENT_TELEGRAM_BOT_TOKEN is required")

    client = TelegramBotClient(
        settings.telegram_bot_token,
        poll_timeout_sec=settings.telegram_poll_timeout_sec,
    )
    updates = await client.get_updates()
    if not updates:
        print("No Telegram updates found yet. Send a message to your bot first, then rerun this command.")
        return

    seen: set[tuple[str, str]] = set()
    for update in updates:
        pair = (update.message.sender_id, update.message.chat_id)
        if pair in seen:
            continue
        seen.add(pair)
        print(
            json.dumps(
                {
                    "sender_id": update.message.sender_id,
                    "chat_id": update.message.chat_id,
                    "text": update.message.text,
                    "timestamp": update.message.timestamp.isoformat(),
                },
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    asyncio.run(main())
