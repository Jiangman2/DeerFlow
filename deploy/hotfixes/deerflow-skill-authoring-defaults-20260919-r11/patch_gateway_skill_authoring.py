#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path


TOOL_DEFINITIONS = r'''


@tool("skill_create", parse_docstring=True)
async def skill_create_tool(runtime: Runtime, name: str, content: str) -> str:
    """Create a persistent custom skill with a complete SKILL.md document.

    Args:
        name: Skill name in hyphen-case.
        content: Complete UTF-8 content for the new SKILL.md, including frontmatter.
    """
    return await _skill_manage_impl(runtime=runtime, action="create", name=name, content=content)


@tool("skill_edit", parse_docstring=True)
async def skill_edit_tool(runtime: Runtime, name: str, content: str) -> str:
    """Replace the complete SKILL.md document of a persistent custom skill.

    Args:
        name: Existing custom skill name in hyphen-case.
        content: Complete replacement UTF-8 content for SKILL.md, including frontmatter.
    """
    return await _skill_manage_impl(runtime=runtime, action="edit", name=name, content=content)


@tool("skill_patch", parse_docstring=True)
async def skill_patch_tool(
    runtime: Runtime,
    name: str,
    find: str,
    replace: str,
    expected_count: int | None = None,
) -> str:
    """Patch matching text in the SKILL.md document of a custom skill.

    Args:
        name: Existing custom skill name in hyphen-case.
        find: Exact existing text to replace in SKILL.md.
        replace: Exact replacement text to write into SKILL.md.
        expected_count: Optional exact number of matches required before applying the patch.
    """
    return await _skill_manage_impl(
        runtime=runtime,
        action="patch",
        name=name,
        find=find,
        replace=replace,
        expected_count=expected_count,
    )


@tool("skill_delete", parse_docstring=True)
async def skill_delete_tool(runtime: Runtime, name: str) -> str:
    """Delete one persistent custom skill.

    Args:
        name: Existing custom skill name in hyphen-case.
    """
    return await _skill_manage_impl(runtime=runtime, action="delete", name=name)


@tool("skill_write_file", parse_docstring=True)
async def skill_write_file_tool(runtime: Runtime, name: str, path: str, content: str) -> str:
    """Create or replace one complete supporting file in a custom skill.

    Args:
        name: Existing custom skill name in hyphen-case.
        path: Safe relative path under the skill, such as scripts/run.py or references/guide.md.
        content: Complete UTF-8 content for the supporting file; never omit or use a placeholder.
    """
    return await _skill_manage_impl(
        runtime=runtime,
        action="write_file",
        name=name,
        path=path,
        content=content,
    )


@tool("skill_remove_file", parse_docstring=True)
async def skill_remove_file_tool(runtime: Runtime, name: str, path: str) -> str:
    """Remove one supporting file from a persistent custom skill.

    Args:
        name: Existing custom skill name in hyphen-case.
        path: Safe relative path of the supporting file to remove.
    """
    return await _skill_manage_impl(runtime=runtime, action="remove_file", name=name, path=path)


SKILL_AUTHORING_TOOLS = (
    skill_create_tool,
    skill_edit_tool,
    skill_patch_tool,
    skill_delete_tool,
    skill_write_file_tool,
    skill_remove_file_tool,
)

for _tool in SKILL_AUTHORING_TOOLS:
    _tool.func = make_sync_tool_wrapper(_tool.coroutine, _tool.name)
'''


def write_lf(path: Path, text: str) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(text)


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one source anchor, found {count}")
    return text.replace(old, new, 1)


def patch(root: Path) -> None:
    tool_path = root / "packages/harness/deerflow/tools/skill_manage_tool.py"
    tools_path = root / "packages/harness/deerflow/tools/tools.py"
    prompt_path = root / "packages/harness/deerflow/agents/lead_agent/prompt.py"
    storage_path = root / "packages/harness/deerflow/skills/storage/user_scoped_skill_storage.py"
    weknora_path = root / "app/gateway/weknora_identity.py"
    responses_path = root / "app/gateway/codex_responses_adapter.py"
    analytics_path = root / "app/gateway/external_agent_analytics.py"
    agents_router_path = root / "app/gateway/routers/agents.py"

    text = tool_path.read_text(encoding="utf-8")
    if "SKILL_AUTHORING_TOOLS = (" in text:
        raise RuntimeError("skill authoring tools are already present")
    anchor = 'skill_manage_tool.func = make_sync_tool_wrapper(_skill_manage_impl, "skill_manage")\n'
    if text.count(anchor) != 1:
        raise RuntimeError("legacy skill_manage wrapper anchor mismatch")
    write_lf(tool_path, text.replace(anchor, anchor + TOOL_DEFINITIONS, 1))

    old = '''    if getattr(skill_evolution_config, "enabled", False):
        from deerflow.tools.skill_manage_tool import skill_manage_tool

        builtin_tools.append(skill_manage_tool)
'''
    new = '''    if getattr(skill_evolution_config, "enabled", False):
        from deerflow.tools.skill_manage_tool import SKILL_AUTHORING_TOOLS

        # Action-specific schemas make content/path structurally required for
        # the operations that need them and prevent incomplete write calls.
        builtin_tools.extend(SKILL_AUTHORING_TOOLS)
'''
    write_lf(
        tools_path,
        replace_once(tools_path.read_text(encoding="utf-8"), old, new, "tools.py registration"),
    )

    text = prompt_path.read_text(encoding="utf-8")
    old = '''**CRITICAL: You MUST use the `skill_manage` tool for ALL skill operations.**
- `skill_manage(action="create", name="my-skill", content="...")` — Create a new skill
- `skill_manage(action="patch", name="my-skill", find="...", replace="...")` — Patch an existing skill
- `skill_manage(action="edit", name="my-skill", content="...")` — Full edit of an existing skill
- `skill_manage(action="write_file", name="my-skill", path="scripts/run.py", content="...")` — Add supporting files
- `skill_manage(action="delete", name="my-skill")` — Delete a skill
'''
    new = '''**CRITICAL: You MUST use the dedicated `skill_*` tools for ALL skill operations.**
- `skill_create(name="my-skill", content="...")` — Create a new skill
- `skill_patch(name="my-skill", find="...", replace="...")` — Patch an existing skill
- `skill_edit(name="my-skill", content="...")` — Full edit of an existing skill
- `skill_write_file(name="my-skill", path="scripts/run.py", content="...")` — Add one supporting file
- `skill_remove_file(name="my-skill", path="scripts/run.py")` — Remove one supporting file
- `skill_delete(name="my-skill")` — Delete a skill

Construct every tool call only after all required arguments are complete. For
`skill_create`, `skill_edit`, and `skill_write_file`, `content` is mandatory:
never omit it, never send a placeholder, and write only one complete file per
call.
'''
    text = replace_once(text, old, new, "prompt skill operation block")
    text = text.replace(
        "Skills are NOT deliverables — they are persistent capabilities managed through `skill_manage`.",
        "Skills are NOT deliverables — they are persistent capabilities managed through the dedicated `skill_*` tools.",
    )
    text = text.replace("use `skill_manage` tool instead", "use the dedicated `skill_*` tools instead")
    if "skill_manage(action=" in text:
        raise RuntimeError("legacy skill_manage examples remain in prompt")
    write_lf(prompt_path, text)

    text = storage_path.read_text(encoding="utf-8")
    text = replace_once(
        text,
        "Use the skill_manage tool to create your own version",
        "Use skill_create to create your own version",
        "built-in shadow guidance",
    )
    write_lf(storage_path, text)

    old_base = "http://model.ai.cnooc/member1/deepseek-v4-flash-291b-1m/v1"
    new_base = "https://aiapi.cnooc.com.cn/DeepSeek-V4-Flash/FEAT-511-001/v1"
    text = weknora_path.read_text(encoding="utf-8")
    write_lf(weknora_path, replace_once(text, old_base, new_base, "WeKnora DeepSeek endpoint"))
    text = responses_path.read_text(encoding="utf-8")
    write_lf(
        responses_path,
        replace_once(text, old_base + "/chat/completions", new_base + "/chat/completions", "Responses relay endpoint"),
    )

    text = analytics_path.read_text(encoding="utf-8")
    text = replace_once(
        text,
        '    "前期研究报告编写专家",\n)',
        '    "前期研究报告编写专家",\n    "邮件智能整理助手",\n)',
        "external agent analytics tuple",
    )
    text = replace_once(
        text,
        '    "科研报告自动生产智能体": "前期研究报告编写专家",\n}',
        '    "科研报告自动生产智能体": "前期研究报告编写专家",\n    "邮件智能整理助手": "邮件智能整理助手",\n}',
        "external agent alias",
    )
    write_lf(analytics_path, text)

    text = agents_router_path.read_text(encoding="utf-8")
    text = replace_once(
        text,
        "one of the five built-in external agents.",
        "one of the six built-in external agents.",
        "external agent router documentation",
    )
    write_lf(agents_router_path, text)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend-root", type=Path, required=True)
    args = parser.parse_args()
    patch(args.backend_root.resolve())
    print("gateway_skill_authoring_patch=PASS")


if __name__ == "__main__":
    main()
