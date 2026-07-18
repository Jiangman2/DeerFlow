from types import SimpleNamespace

from deerflow.agents.lead_agent.agent import _restore_task_tool_for_subagent_mode


def test_subagent_mode_restores_task_removed_by_skill_tool_policy() -> None:
    raw_tools = [SimpleNamespace(name="read_file"), SimpleNamespace(name="task"), SimpleNamespace(name="bash")]
    filtered_tools = [raw_tools[0]]

    restored = _restore_task_tool_for_subagent_mode(raw_tools, filtered_tools, subagent_enabled=True)

    assert [tool.name for tool in restored] == ["read_file", "task"]


def test_non_subagent_mode_preserves_skill_tool_policy_result() -> None:
    raw_tools = [SimpleNamespace(name="read_file"), SimpleNamespace(name="task")]
    filtered_tools = [raw_tools[0]]

    assert _restore_task_tool_for_subagent_mode(raw_tools, filtered_tools, subagent_enabled=False) == filtered_tools
