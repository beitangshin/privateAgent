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

    messages = backend._build_react_messages(
        [],
        [{"name": "web_search", "description": "search", "input_schema": {}}],
        None,
        [],
    )

    system_prompt = messages[0]["content"]
    assert "You are an agent that solves tasks using the ReAct pattern." in system_prompt
    assert "Thought:" in system_prompt
    assert "Action Input: a JSON object" in system_prompt
    assert "prefer 1-3 focused searches" in system_prompt
    assert "local market's search vocabulary" in system_prompt
    assert "concrete listing or primary-source domains" in system_prompt


def test_plan_prompt_prefers_project_discovery_tools_for_run_requests(tmp_path: Path) -> None:
    backend = _backend(tmp_path)

    messages = backend._build_react_messages(
        [],
        [
            {"name": "find_paths", "description": "find local paths", "input_schema": {}},
            {"name": "inspect_project", "description": "inspect a project", "input_schema": {}},
            {"name": "run_shell_command", "description": "run shell", "input_schema": {}},
        ],
        None,
        [],
    )

    system_prompt = messages[0]["content"]
    assert "first use find_paths to locate it" in system_prompt
    assert "then inspect_project on the best match" in system_prompt
    assert "Do not waste turns repeatedly listing broad parent directories" in system_prompt
    assert "Prefer inspect_project before shell execution" in system_prompt


def test_plan_prompt_includes_goal_inference_rules(tmp_path: Path) -> None:
    backend = _backend(tmp_path)

    messages = backend._build_react_messages(
        [],
        [{"name": "find_paths", "description": "find local paths", "input_schema": {}}],
        {"task_frame": {"normalized_goal": "run hiltest"}},
        [],
    )

    system_prompt = messages[0]["content"]
    assert "Treat session_context.task_frame as the controller's distilled view" in system_prompt
    assert "Infer the user's actual goal before acting." in system_prompt
    assert "Prefer high-signal evidence over noisy candidate matches." in system_prompt
    assert "Expand hidden substeps mentally" in system_prompt


def test_plan_prompt_includes_code_edit_workflow_guidance(tmp_path: Path) -> None:
    backend = _backend(tmp_path)

    messages = backend._build_react_messages(
        [],
        [
            {"name": "project_map", "description": "map project", "input_schema": {}},
            {"name": "patch_file", "description": "patch file", "input_schema": {}},
            {"name": "run_shell_command", "description": "run shell", "input_schema": {}},
        ],
        None,
        [],
    )

    system_prompt = messages[0]["content"]
    assert "first inspect the project with project_map" in system_prompt
    assert "use patch_file for minimal exact edits" in system_prompt
    assert "Do not patch blindly before reading" in system_prompt
