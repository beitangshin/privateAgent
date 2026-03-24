import pytest

from private_agent.auth import AuthorizationError, SenderAuthorizer


def test_allowlisted_sender_is_accepted() -> None:
    SenderAuthorizer({"alice"}).verify("alice")


def test_unknown_sender_is_rejected() -> None:
    with pytest.raises(AuthorizationError):
        SenderAuthorizer({"alice"}).verify("bob")


def test_unknown_chat_is_rejected_when_chat_allowlist_exists() -> None:
    with pytest.raises(AuthorizationError):
        SenderAuthorizer({"alice"}, {"chat-1"}).verify_chat("chat-2")
