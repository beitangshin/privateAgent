from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass(slots=True)
class Attachment:
    name: str
    content_type: str | None = None
    url: str | None = None


@dataclass(slots=True)
class IncomingMessage:
    platform: str
    sender_id: str
    chat_id: str
    message_id: str
    text: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    attachments: list[Attachment] = field(default_factory=list)
