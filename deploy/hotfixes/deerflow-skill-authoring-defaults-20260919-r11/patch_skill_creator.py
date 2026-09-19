#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one source anchor, found {count}")
    return text.replace(old, new, 1)


def patch_text(text: str) -> str:
    text = replace_once(
        text,
        "If you are running inside a **DeerFlow** sandboxed agent environment (you have access to the `skill_manage` tool),",
        "If you are running inside a **DeerFlow** sandboxed agent environment (you have access to the dedicated `skill_*` authoring tools),",
        "environment introduction",
    )
    text = replace_once(
        text,
        "Skills must be persisted through the dedicated `skill_manage` tool so they are stored",
        "Skills must be persisted through the dedicated `skill_*` authoring tools so they are stored",
        "persistence guidance",
    )
    old_table = '''### Use `skill_manage` for all skill file operations

| Operation | skill_manage action | Example |
|-----------|---------------------|---------|
| Create a new skill | `action="create"` | `skill_manage(action="create", name="my-skill", content="---\\nname: my-skill\\n---\\n...")` |
| Replace entire SKILL.md | `action="edit"` | `skill_manage(action="edit", name="my-skill", content="updated SKILL.md")` |
| Partial edit (find & replace) | `action="patch"` | `skill_manage(action="patch", name="my-skill", find="old text", replace="new text")` |
| Delete a skill | `action="delete"` | `skill_manage(action="delete", name="my-skill")` |
| Add a supporting file | `action="write_file"` | `skill_manage(action="write_file", name="my-skill", path="scripts/helper.py", content="...")` |
| Remove a supporting file | `action="remove_file"` | `skill_manage(action="remove_file", name="my-skill", path="scripts/helper.py")` |
'''
    new_table = '''### Use the dedicated `skill_*` tools for all skill file operations

| Operation | Tool | Example |
|-----------|------|---------|
| Create a new skill | `skill_create` | `skill_create(name="my-skill", content="---\\nname: my-skill\\n---\\n...")` |
| Replace entire SKILL.md | `skill_edit` | `skill_edit(name="my-skill", content="updated SKILL.md")` |
| Partial edit (find & replace) | `skill_patch` | `skill_patch(name="my-skill", find="old text", replace="new text")` |
| Delete a skill | `skill_delete` | `skill_delete(name="my-skill")` |
| Add a supporting file | `skill_write_file` | `skill_write_file(name="my-skill", path="scripts/helper.py", content="...")` |
| Remove a supporting file | `skill_remove_file` | `skill_remove_file(name="my-skill", path="scripts/helper.py")` |
'''
    text = replace_once(text, old_table, new_table, "operation table")
    text = replace_once(
        text,
        "Always use `skill_manage` instead.",
        "Always use the matching dedicated `skill_*` tool instead.",
        "write boundary",
    )
    text = replace_once(
        text,
        "In DeerFlow, `skill_manage` already persists the skill",
        "In DeerFlow, the dedicated authoring tools already persist the skill",
        "package guidance",
    )
    text = replace_once(
        text,
        "use `skill_manage(action=\"edit\")` or `skill_manage(action=\"patch\")`. Do NOT copy to `/tmp/` first — `skill_manage` handles",
        "use `skill_edit` or `skill_patch`. Do NOT copy to `/tmp/` first — the tools handle",
        "update guidance",
    )
    text = replace_once(
        text,
        "### Workflow in DeerFlow\n",
        "7. **Complete arguments before calling a tool**: `content` is mandatory for `skill_create`, `skill_edit`, and `skill_write_file`. Never call one of them without the complete content, never send placeholder content, and write only one complete supporting file per `skill_write_file` call.\n\n### Workflow in DeerFlow\n",
        "complete argument rule",
    )
    text = replace_once(
        text,
        '**Call `skill_manage(action="create", name=<name>, content=<SKILL.md>)`**',
        '**Call `skill_create(name=<name>, content=<SKILL.md>)`**',
        "create workflow",
    )
    text = replace_once(
        text,
        '**Call `skill_manage(action="edit")` or `skill_manage(action="patch")`**',
        '**Call `skill_edit` or `skill_patch`**',
        "edit workflow",
    )
    if "skill_manage(action=" in text or "`skill_manage` tool" in text:
        raise RuntimeError("legacy skill_manage authoring guidance remains")
    return text


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--path", type=Path, required=True)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.check == args.apply:
        raise SystemExit("select exactly one of --check or --apply")
    source = args.path.read_text(encoding="utf-8")
    target = patch_text(source)
    digest = hashlib.sha256(target.encode("utf-8")).hexdigest()
    if args.apply:
        temporary = args.path.with_name(args.path.name + ".new")
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            stream.write(target)
        os.replace(temporary, args.path)
    print(f"skill_creator_dedicated_tool_patch={'CHECK' if args.check else 'APPLIED'} sha256={digest}")


if __name__ == "__main__":
    main()
