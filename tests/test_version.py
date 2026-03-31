from pathlib import Path

from private_agent.agent import AgentService
from private_agent.audit import AuditLogger
from private_agent.auth import SenderAuthorizer
from private_agent.executor import Executor
from private_agent.models import MockModelBackend
from private_agent.policy import PolicyEngine
from private_agent.storage import StateStore
from private_agent.tools import ToolContext, ToolRegistry, build_builtin_tools
from private_agent.transport import IncomingMessage
from private_agent.version import APP_VERSION


def test_version_command_returns_current_version(tmp_path: Path) -> None:
    allowed_root = tmp_path / "allowed"
    allowed_root.mkdir()
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    notes_dir = tmp_path / "notes"
    inventory_sync_dir = tmp_path / "inventory_sync"
    registry = ToolRegistry(build_builtin_tools())
    context = ToolContext(
        allowed_roots=(allowed_root.resolve(),),
        allowed_repos={"demo": repo_root.resolve()},
        notes_dir=notes_dir.resolve(),
        inventory_sync_dir=inventory_sync_dir.resolve(),
        safe_mode=True,
        enable_network_tools=False,
        enable_desktop_tools=False,
        enable_web_search=False,
        web_search_allowed_domains=(),
        web_search_max_results=5,
        model_backend_name="mock",
    )
    service = AgentService(
        authorizer=SenderAuthorizer({"user-1"}, {"chat-1"}),
        registry=registry,
        policy=PolicyEngine(safe_mode=True),
        executor=Executor(registry, context),
        audit=AuditLogger(tmp_path / "audit.log"),
        state_store=StateStore(tmp_path / "state.json"),
        model_backend=MockModelBackend(),
    )
    message = IncomingMessage(
        platform="telegram",
        sender_id="user-1",
        chat_id="chat-1",
        message_id="1",
        text="/version",
    )

    result = service.get_version(message)

    assert result.status == "ok"
    assert result.data == {"version": APP_VERSION}
    assert APP_VERSION in result.message
