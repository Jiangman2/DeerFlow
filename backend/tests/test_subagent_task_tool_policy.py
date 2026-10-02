from types import SimpleNamespace

from deerflow.skills.tool_policy import filter_tools_by_skill_allowed_tools


def test_restrictive_skill_does_not_restore_task_for_subagent_mode() -> None:
    tools = [SimpleNamespace(name="read_file"), SimpleNamespace(name="task"), SimpleNamespace(name="bash")]
    skills = [SimpleNamespace(name="read-only", allowed_tools=("read_file",))]

    filtered = filter_tools_by_skill_allowed_tools(tools, skills)

    assert [tool.name for tool in filtered] == ["read_file"]


def test_restrictive_skill_can_explicitly_allow_task() -> None:
    tools = [SimpleNamespace(name="read_file"), SimpleNamespace(name="task"), SimpleNamespace(name="bash")]
    skills = [SimpleNamespace(name="delegating", allowed_tools=("read_file", "task"))]

    filtered = filter_tools_by_skill_allowed_tools(tools, skills)

    assert [tool.name for tool in filtered] == ["read_file", "task"]
