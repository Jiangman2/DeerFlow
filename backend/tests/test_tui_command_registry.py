"""Tests for the slash-command registry (pure)."""

from deerflow.skills.slash import RESERVED_SLASH_SKILL_NAMES
from deerflow.tui.command_registry import (
    BUILTIN_COMMANDS,
    build_registry,
    filter_commands,
    resolve,
)


def _skills():
    return [
        {"name": "brainstorming", "description": "Explore ideas", "enabled": True},
        {"name": "tdd", "description": "Test driven dev", "enabled": True},
        {"name": "disabled-one", "description": "off", "enabled": False},
    ]


def test_build_registry_includes_all_builtins():
    registry = build_registry([])
    names = {c.name for c in registry}
    for builtin in BUILTIN_COMMANDS:
        assert builtin.name in names


def test_build_registry_adds_enabled_skill_commands_only():
    registry = build_registry(_skills())
    skill_names = {c.name for c in registry if c.category == "skill"}
    assert "brainstorming" in skill_names
    assert "tdd" in skill_names
    assert "disabled-one" not in skill_names


def test_filter_empty_query_returns_all():
    registry = build_registry([])
    assert filter_commands(registry, "") == registry


def test_filter_matches_name_substring_case_insensitive():
    registry = build_registry([])
    results = filter_commands(registry, "MOD")
    assert any(c.name == "model" for c in results)


def test_filter_matches_description():
    registry = build_registry(_skills())
    results = filter_commands(registry, "explore")
    assert any(c.name == "brainstorming" for c in results)


def test_filter_ranks_prefix_matches_before_substring():
    registry = build_registry([])
    results = filter_commands(registry, "me")
    # "memory" (prefix) should rank above a command that only contains "me"
    names = [c.name for c in results]
    assert "memory" in names
    assert names.index("memory") == 0


def test_resolve_plain_text_is_message():
    res = resolve("hello there")
    assert res.kind == "message"
    assert res.text == "hello there"


def test_resolve_builtin_command():
    res = resolve("/model")
    assert res.kind == "builtin"
    assert res.name == "model"


def test_resolve_builtin_with_args():
    res = resolve("/resume thread-123")
    assert res.kind == "builtin"
    assert res.name == "resume"
    assert res.args == "thread-123"


def test_resolve_skill_activation():
    res = resolve("/tdd write the test first", skills=["tdd"])
    assert res.kind == "skill"
    assert res.name == "tdd"
    assert res.args == "write the test first"


def test_resolve_unknown_command():
    res = resolve("/definitely-not-a-command", skills=["tdd"])
    assert res.kind == "unknown"
    assert res.name == "definitely-not-a-command"


def test_resolve_bare_slash_is_unknown_empty():
    res = resolve("/")
    assert res.kind == "unknown"


def test_goal_is_builtin_command():
    resolved = resolve("/goal finish the implementation")

    assert resolved.kind == "builtin"
    assert resolved.name == "goal"
    assert resolved.args == "finish the implementation"


def test_goal_builtin_takes_precedence_over_skill():
    registry = build_registry([{"name": "goal", "description": "skill", "enabled": True}])

    assert [command.name for command in registry].count("goal") == 1
    assert resolve("/goal finish", skills=["goal"]).kind == "builtin"


def test_registry_excludes_reserved_skill_names_except_context():
    registry = build_registry([{"name": name, "description": "reserved", "enabled": True} for name in RESERVED_SLASH_SKILL_NAMES])

    skill_names = {command.name for command in registry if command.category == "skill"}
    assert skill_names == {"context"}


def test_resolve_rejects_reserved_skill_commands_but_keeps_context_tasks():
    reserved_names = sorted(RESERVED_SLASH_SKILL_NAMES)

    for name in reserved_names:
        resolved = resolve(f"/{name} task", skills=reserved_names)
        if name == "context":
            assert resolved.kind == "skill"
        else:
            assert resolved.kind != "skill", name

    assert resolve("/context compact", skills=["context"]).kind == "unknown"
