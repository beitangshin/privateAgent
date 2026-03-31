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


def _parse_int(raw: str | None, *, default: int, minimum: int, maximum: int) -> int:
    if raw is None or not raw.strip():
        return default
    value = int(raw.strip())
    return max(minimum, min(maximum, value))


def _parse_repo_map(raw: str | None) -> dict[str, Path]:
    if not raw:
        return {}
    repos: dict[str, Path] = {}
    for part in raw.split(","):
        item = part.strip()
        if not item or "=" not in item:
            continue
        name, path = item.split("=", maxsplit=1)
        repo_name = name.strip()
        repo_path = path.strip()
        if repo_name and repo_path:
            repos[repo_name] = Path(repo_path).expanduser().resolve()
    return repos


@dataclass(slots=True)
class Settings:
    allowed_senders: set[str]
    allowed_chat_ids: set[str]
    allowed_roots: tuple[Path, ...]
    allowed_repos: dict[str, Path]
    notes_dir: Path
    knowledge_base_dir: Path
    audit_log_path: Path
    model_call_log_path: Path
    state_store_path: Path
    safe_mode: bool
    enable_network_tools: bool
    enable_desktop_tools: bool
    enable_web_search: bool
    web_search_allowed_domains: tuple[str, ...]
    web_search_max_results: int
    model_backend: str
    deepseek_api_key: str | None
    deepseek_base_url: str
    deepseek_model: str
    model_prompt_version: str
    telegram_bot_token: str | None
    telegram_poll_timeout_sec: int
    conversation_history_messages: int
    knowledge_max_snippets: int
    enable_inventory_sync: bool
    inventory_sync_bind_host: str
    inventory_sync_port: int
    inventory_sync_token: str | None
    inventory_sync_dir: Path
    inventory_sync_by_source_ip: bool


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
    knowledge_base_dir = Path(
        os.getenv("PRIVATE_AGENT_KNOWLEDGE_BASE_DIR", str(Path.cwd() / "data" / "knowledge"))
    ).expanduser().resolve()
    model_call_log_path = Path(
        os.getenv("PRIVATE_AGENT_MODEL_CALL_LOG_PATH", str(Path.cwd() / "data" / "model_calls.log"))
    ).expanduser().resolve()
    state_store_path = Path(
        os.getenv("PRIVATE_AGENT_STATE_STORE_PATH", str(Path.cwd() / "data" / "state.json"))
    ).expanduser().resolve()
    inventory_sync_dir = Path(
        os.getenv("PRIVATE_AGENT_INVENTORY_SYNC_DIR", str(Path.cwd() / "data" / "inventory_sync"))
    ).expanduser().resolve()

    return Settings(
        allowed_senders=set(_split_csv(os.getenv("PRIVATE_AGENT_ALLOWED_SENDERS"))),
        allowed_chat_ids=set(_split_csv(os.getenv("PRIVATE_AGENT_ALLOWED_CHAT_IDS"))),
        allowed_roots=allowed_roots,
        allowed_repos=_parse_repo_map(os.getenv("PRIVATE_AGENT_ALLOWED_REPOS")),
        notes_dir=notes_dir,
        knowledge_base_dir=knowledge_base_dir,
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
        enable_web_search=_parse_bool(
            os.getenv("PRIVATE_AGENT_ENABLE_WEB_SEARCH"), default=False
        ),
        web_search_allowed_domains=tuple(
            _split_csv(os.getenv("PRIVATE_AGENT_WEB_SEARCH_ALLOWED_DOMAINS"))
        ),
        web_search_max_results=_parse_int(
            os.getenv("PRIVATE_AGENT_WEB_SEARCH_MAX_RESULTS"),
            default=5,
            minimum=1,
            maximum=10,
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
        conversation_history_messages=_parse_int(
            os.getenv("PRIVATE_AGENT_CONVERSATION_HISTORY_MESSAGES"),
            default=12,
            minimum=0,
            maximum=40,
        ),
        knowledge_max_snippets=_parse_int(
            os.getenv("PRIVATE_AGENT_KNOWLEDGE_MAX_SNIPPETS"),
            default=4,
            minimum=1,
            maximum=10,
        ),
        enable_inventory_sync=_parse_bool(
            os.getenv("PRIVATE_AGENT_ENABLE_INVENTORY_SYNC"), default=False
        ),
        inventory_sync_bind_host=os.getenv("PRIVATE_AGENT_INVENTORY_SYNC_BIND_HOST", "0.0.0.0").strip()
        or "0.0.0.0",
        inventory_sync_port=_parse_int(
            os.getenv("PRIVATE_AGENT_INVENTORY_SYNC_PORT"),
            default=8765,
            minimum=1,
            maximum=65535,
        ),
        inventory_sync_token=os.getenv("PRIVATE_AGENT_INVENTORY_SYNC_TOKEN"),
        inventory_sync_dir=inventory_sync_dir,
        inventory_sync_by_source_ip=_parse_bool(
            os.getenv("PRIVATE_AGENT_INVENTORY_SYNC_BY_SOURCE_IP"), default=False
        ),
    )
