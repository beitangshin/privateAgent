from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _load_dotenv() -> None:
    env_path = Path.cwd() / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.lstrip("\ufeff").strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", maxsplit=1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def _split_csv(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]


def _parse_bool(raw: str | None, *, default: bool) -> bool:
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(slots=True)
class Settings:
    allowed_senders: set[str]
    allowed_chat_ids: set[str]
    allowed_roots: tuple[Path, ...]
    notes_dir: Path
    audit_log_path: Path
    model_call_log_path: Path
    state_store_path: Path
    safe_mode: bool
    enable_network_tools: bool
    enable_desktop_tools: bool
    model_backend: str
    deepseek_api_key: str | None
    deepseek_base_url: str
    deepseek_model: str
    model_prompt_version: str
    telegram_bot_token: str | None
    telegram_poll_timeout_sec: int


def load_settings() -> Settings:
    _load_dotenv()
    allowed_roots = tuple(
        Path(item).expanduser().resolve()
        for item in _split_csv(os.getenv("PRIVATE_AGENT_ALLOWED_ROOTS"))
    )
    notes_dir = Path(
        os.getenv("PRIVATE_AGENT_NOTES_DIR", str(Path.cwd() / "data" / "notes"))
    ).expanduser().resolve()
    audit_log_path = Path(
        os.getenv("PRIVATE_AGENT_AUDIT_LOG_PATH", str(Path.cwd() / "data" / "audit.log"))
    ).expanduser().resolve()
    model_call_log_path = Path(
        os.getenv("PRIVATE_AGENT_MODEL_CALL_LOG_PATH", str(Path.cwd() / "data" / "model_calls.log"))
    ).expanduser().resolve()
    state_store_path = Path(
        os.getenv("PRIVATE_AGENT_STATE_STORE_PATH", str(Path.cwd() / "data" / "state.json"))
    ).expanduser().resolve()

    return Settings(
        allowed_senders=set(_split_csv(os.getenv("PRIVATE_AGENT_ALLOWED_SENDERS"))),
        allowed_chat_ids=set(_split_csv(os.getenv("PRIVATE_AGENT_ALLOWED_CHAT_IDS"))),
        allowed_roots=allowed_roots,
        notes_dir=notes_dir,
        audit_log_path=audit_log_path,
        model_call_log_path=model_call_log_path,
        state_store_path=state_store_path,
        safe_mode=_parse_bool(os.getenv("PRIVATE_AGENT_SAFE_MODE"), default=True),
        enable_network_tools=_parse_bool(
            os.getenv("PRIVATE_AGENT_ENABLE_NETWORK_TOOLS"), default=False
        ),
        enable_desktop_tools=_parse_bool(
            os.getenv("PRIVATE_AGENT_ENABLE_DESKTOP_TOOLS"), default=False
        ),
        model_backend=os.getenv("PRIVATE_AGENT_MODEL_BACKEND", "mock").strip() or "mock",
        deepseek_api_key=os.getenv("PRIVATE_AGENT_DEEPSEEK_API_KEY"),
        deepseek_base_url=(
            os.getenv("PRIVATE_AGENT_DEEPSEEK_BASE_URL", "https://api.deepseek.com").strip()
            or "https://api.deepseek.com"
        ),
        deepseek_model=os.getenv("PRIVATE_AGENT_DEEPSEEK_MODEL", "deepseek-chat").strip()
        or "deepseek-chat",
        model_prompt_version=os.getenv("PRIVATE_AGENT_MODEL_PROMPT_VERSION", "v1").strip()
        or "v1",
        telegram_bot_token=os.getenv("PRIVATE_AGENT_TELEGRAM_BOT_TOKEN"),
        telegram_poll_timeout_sec=int(
            os.getenv("PRIVATE_AGENT_TELEGRAM_POLL_TIMEOUT_SEC", "20")
        ),
    )
