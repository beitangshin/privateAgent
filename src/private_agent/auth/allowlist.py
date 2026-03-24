from __future__ import annotations


class AuthorizationError(PermissionError):
    """Raised when a sender is not approved."""


class SenderAuthorizer:
    def __init__(self, allowed_senders: set[str], allowed_chat_ids: set[str] | None = None) -> None:
        self._allowed_senders = allowed_senders
        self._allowed_chat_ids = allowed_chat_ids or set()

    def verify(self, sender_id: str) -> None:
        if sender_id not in self._allowed_senders:
            raise AuthorizationError(f"sender '{sender_id}' is not allowlisted")

    def verify_chat(self, chat_id: str) -> None:
        if self._allowed_chat_ids and chat_id not in self._allowed_chat_ids:
            raise AuthorizationError(f"chat '{chat_id}' is not allowlisted")
