"""Regression test: tool args schemas must not emit Pydantic serialization warnings.

DeerFlow tools annotate their runtime parameter as ``Runtime``
(``deerflow.tools.types.Runtime`` = ``ToolRuntime[dict[str, Any], ThreadState]``)
so the LangChain tool framework injects the runtime automatically.
When the inner ``Runtime.context`` field is left as the unbound ``ContextT``
TypeVar (default ``None``), Pydantic's ``model_dump()`` on the auto-generated
args schema emits a ``PydanticSerializationUnexpectedValue`` warning on every
tool call because the actual context DeerFlow installs is a dict. Using the
``Runtime`` alias (which binds the context to ``dict[str, Any]``) keeps
Pydantic's serialization expectations aligned with reality.
"""

from __future__ import annotations

import warnings

import pytest
from langchain.tools import ToolRuntime

from deerflow.sandbox.tools import (
    bash_tool,
    glob_tool,
    grep_tool,
    ls_tool,
    read_file_tool,
    str_replace_tool,
    write_file_tool,
)
from deerflow.tools.builtins.present_file_tool import present_file_tool
from deerflow.tools.builtins.setup_agent_tool import setup_agent
from deerflow.tools.builtins.task_tool import task_tool
from deerflow.tools.builtins.update_agent_tool import update_agent
from deerflow.tools.builtins.view_image_tool import view_image_tool
from deerflow.tools.skill_manage_tool import SKILL_AUTHORING_TOOLS, skill_manage_tool


def _skill_authoring_tool_args(tool_name: str) -> dict[str, object]:
    return {
        "skill_create": {"name": "demo", "content": "---\nname: demo\n---\n"},
        "skill_edit": {"name": "demo", "content": "---\nname: demo\n---\n"},
        "skill_patch": {"name": "demo", "find": "old", "replace": "new"},
        "skill_delete": {"name": "demo"},
        "skill_write_file": {"name": "demo", "path": "scripts/run.py", "content": "print('ok')\n"},
        "skill_remove_file": {"name": "demo", "path": "scripts/run.py"},
    }[tool_name]


def _make_runtime(context: dict) -> ToolRuntime:
    return ToolRuntime(
        state={"sandbox": {"sandbox_id": "local"}, "thread_data": {}},
        context=context,
        config={"configurable": {"thread_id": context.get("thread_id", "thread-1")}},
        stream_writer=lambda _: None,
        tools=[],
        tool_call_id="call-1",
        store=None,
    )


_TOOL_CASES = [
    (bash_tool, {"description": "list", "command": "ls"}),
    (ls_tool, {"description": "list", "path": "/tmp"}),
    (glob_tool, {"description": "find", "pattern": "*.py", "path": "/tmp"}),
    (grep_tool, {"description": "search", "pattern": "x", "path": "/tmp"}),
    (read_file_tool, {"description": "read", "path": "/tmp/x"}),
    (write_file_tool, {"description": "write", "path": "/tmp/x", "content": "hi"}),
    (str_replace_tool, {"description": "replace", "path": "/tmp/x", "old_str": "a", "new_str": "b"}),
    (present_file_tool, {"filepaths": ["/tmp/x"], "tool_call_id": "call-1"}),
    (view_image_tool, {"image_path": "/tmp/img.png", "tool_call_id": "call-1"}),
    (task_tool, {"description": "do", "prompt": "go", "subagent_type": "general-purpose", "tool_call_id": "call-1"}),
    (skill_manage_tool, {"action": "list", "name": "demo"}),
    *[(tool_obj, _skill_authoring_tool_args(tool_obj.name)) for tool_obj in SKILL_AUTHORING_TOOLS],
    (setup_agent, {"soul": "s", "description": "d"}),
    (update_agent, {}),
]


@pytest.mark.parametrize(
    ("tool_obj", "extra_args"),
    _TOOL_CASES,
    ids=[case[0].name for case in _TOOL_CASES],
)
def test_tool_args_schema_does_not_emit_pydantic_context_warning(tool_obj, extra_args) -> None:
    """``model_dump()`` of the auto-generated args_schema must not warn about ``context``.

    The model_dump path is hit by LangChain's ``BaseTool._parse_input`` on every tool
    invocation (see langchain_core/tools/base.py:712), so any warning here would fire
    once per tool call and pollute production logs.
    """
    schema = tool_obj.args_schema
    assert schema is not None, f"{tool_obj.name} has no args_schema"

    runtime_obj = _make_runtime({"thread_id": "thread-1", "sandbox_id": "local"})
    payload = {**extra_args, "runtime": runtime_obj}

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        validated = schema.model_validate(payload)
        validated.model_dump()

    pydantic_warnings = [w for w in caught if "PydanticSerializationUnexpectedValue" in str(w.message)]
    assert not pydantic_warnings, f"{tool_obj.name} args_schema.model_dump() emitted Pydantic context serialization warnings: {[str(w.message) for w in pydantic_warnings]}"


def test_write_file_append_is_discoverable_in_tool_schema() -> None:
    """``append`` must be visible and described in the model-facing tool schema."""
    assert "append" in write_file_tool.description

    append_field = write_file_tool.tool_call_schema.model_fields["append"]
    assert append_field.default is False
    assert append_field.description
    assert "append" in append_field.description


def test_skill_authoring_tools_expose_action_specific_required_parameters() -> None:
    """The model must see every argument required by the selected skill action."""
    expected_required = {
        "skill_create": {"name", "content"},
        "skill_edit": {"name", "content"},
        "skill_patch": {"name", "find", "replace"},
        "skill_delete": {"name"},
        "skill_write_file": {"name", "path", "content"},
        "skill_remove_file": {"name", "path"},
    }

    for tool_obj in SKILL_AUTHORING_TOOLS:
        fields = tool_obj.tool_call_schema.model_fields
        assert "action" not in fields
        assert {name for name, field in fields.items() if field.is_required()} == expected_required[tool_obj.name]


@pytest.mark.parametrize(
    ("tool_obj", "required_args"),
    [
        (bash_tool, {"command": "true"}),
        (ls_tool, {"path": "/tmp"}),
        (glob_tool, {"pattern": "*.py", "path": "/tmp"}),
        (grep_tool, {"pattern": "x", "path": "/tmp"}),
        (read_file_tool, {"path": "/tmp/x"}),
        (write_file_tool, {"path": "/tmp/x", "content": "hi"}),
        (
            str_replace_tool,
            {"path": "/tmp/x", "old_str": "a", "new_str": "b"},
        ),
    ],
    ids=lambda value: getattr(value, "name", None) or str(value),
)
def test_sandbox_tool_descriptions_are_optional_execution_metadata(tool_obj, required_args) -> None:
    """Display metadata must not turn a valid Sandbox action into a failed receipt."""
    fields = tool_obj.tool_call_schema.model_fields

    assert fields["description"].default == ""
    for name in required_args:
        assert fields[name].is_required()
    tool_obj.tool_call_schema.model_validate(required_args)


@pytest.mark.parametrize("tool_obj", [case[0] for case in _TOOL_CASES], ids=[case[0].name for case in _TOOL_CASES])
def test_model_facing_tool_parameters_have_descriptions(tool_obj) -> None:
    """Every model-facing tool parameter should explain when and how to use it."""
    missing_descriptions = [field_name for field_name, field in tool_obj.tool_call_schema.model_fields.items() if not field.description]
    assert missing_descriptions == [], f"{tool_obj.name} has model-facing parameters without descriptions: {missing_descriptions}. Add an Args: section to the tool's docstring and ensure @tool(parse_docstring=True) is set."
