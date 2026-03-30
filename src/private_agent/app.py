from __future__ import annotations

from private_agent.agent import AgentService
from private_agent.audit import AuditLogger
from private_agent.auth import SenderAuthorizer
from private_agent.config import load_settings
from private_agent.executor import Executor
from private_agent.knowledge import LocalKnowledgeBase
from private_agent.models import DeepSeekCloudBackend, MockModelBackend
from private_agent.policy import PolicyEngine
from private_agent.storage import StateStore
from private_agent.sync import InventorySyncStore
from private_agent.tools import ToolContext, ToolRegistry, build_builtin_tools


def build_model_backend(settings: object) -> object:
    if getattr(settings, "model_backend", "mock") == "deepseek_cloud":
        api_key = getattr(settings, "deepseek_api_key", None)
        if not api_key:
            raise RuntimeError(
                "PRIVATE_AGENT_DEEPSEEK_API_KEY is required when PRIVATE_AGENT_MODEL_BACKEND=deepseek_cloud"
            )
        return DeepSeekCloudBackend(
            api_key=api_key,
            model=getattr(settings, "deepseek_model"),
            base_url=getattr(settings, "deepseek_base_url"),
            prompt_version=getattr(settings, "model_prompt_version"),
            model_call_log_path=getattr(settings, "model_call_log_path"),
        )
    return MockModelBackend()


def build_enabled_tool_names(settings: object) -> set[str]:
    enabled = {tool.name for tool in build_builtin_tools()}
    if not getattr(settings, "enable_network_tools", False):
        enabled.discard("get_network_summary")
    if not getattr(settings, "enable_web_search", False):
        enabled.discard("web_search")
    if not getattr(settings, "enable_desktop_tools", False):
        pass
    return enabled


def build_app() -> AgentService:
    settings = load_settings()
    model_backend = build_model_backend(settings)
    tool_context = ToolContext(
        allowed_roots=settings.allowed_roots,
        allowed_repos=settings.allowed_repos,
        notes_dir=settings.notes_dir,
        inventory_sync_dir=settings.inventory_sync_dir,
        safe_mode=settings.safe_mode,
        enable_network_tools=settings.enable_network_tools,
        enable_desktop_tools=settings.enable_desktop_tools,
        enable_web_search=settings.enable_web_search,
        web_search_allowed_domains=settings.web_search_allowed_domains,
        web_search_max_results=settings.web_search_max_results,
        model_backend_name=settings.model_backend,
    )
    registry = ToolRegistry(build_builtin_tools())
    enabled_tool_names = build_enabled_tool_names(settings)
    executor = Executor(registry, tool_context)
    return AgentService(
        authorizer=SenderAuthorizer(settings.allowed_senders, settings.allowed_chat_ids),
        registry=registry,
        policy=PolicyEngine(safe_mode=settings.safe_mode),
        executor=executor,
        audit=AuditLogger(settings.audit_log_path),
        state_store=StateStore(settings.state_store_path),
        model_backend=model_backend,
        enabled_tool_names=enabled_tool_names,
        conversation_history_messages=settings.conversation_history_messages,
        knowledge_base=LocalKnowledgeBase(
            settings.knowledge_base_dir,
            max_snippets=settings.knowledge_max_snippets,
        ),
        inventory_store=InventorySyncStore(
            root=settings.inventory_sync_dir,
            knowledge_root=settings.knowledge_base_dir,
        ),
    )
