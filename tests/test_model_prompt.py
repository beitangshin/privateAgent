from pathlib import Path

from private_agent.models.deepseek_cloud import DeepSeekCloudBackend


def _backend(tmp_path: Path) -> DeepSeekCloudBackend:
    return DeepSeekCloudBackend(
        api_key="test-key",
        model="deepseek-chat",
        base_url="https://api.deepseek.com",
        prompt_version="v1",
        model_call_log_path=tmp_path / "model_calls.log",
    )


def test_plan_prompt_includes_web_search_guidance(tmp_path: Path) -> None:
    backend = _backend(tmp_path)

    messages = backend._build_plan_messages(
        [],
        [{"name": "web_search", "description": "search", "input_schema": {}}],
        None,
    )

    system_prompt = messages[0]["content"]
    assert "prefer 1-3 focused searches" in system_prompt
    assert "local market's search vocabulary" in system_prompt
    assert "concrete listing or primary-source domains" in system_prompt
