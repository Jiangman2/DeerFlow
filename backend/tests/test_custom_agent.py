"""Tests for custom agent support."""

from __future__ import annotations

import base64
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml
from fastapi.testclient import TestClient

from deerflow.config.agents_api_config import AgentsApiConfig, get_agents_api_config, set_agents_api_config


@pytest.fixture(autouse=True)
def _use_isolated_file_agent_store(monkeypatch):
    """Keep legacy file-layout tests independent of a developer config.yaml."""
    from deerflow.persistence.agents.file import FileAgentStore

    store = FileAgentStore()
    monkeypatch.setattr(
        "deerflow.persistence.agents.get_agent_store",
        lambda: store,
    )
    # The router imports the symbol directly and may already be loaded during
    # test collection, so patch that binding as well.
    monkeypatch.setattr(
        "app.gateway.routers.agents.get_agent_store",
        lambda: store,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_paths(base_dir: Path):
    """Return a Paths instance pointing to base_dir."""
    from deerflow.config.paths import Paths

    return Paths(base_dir=base_dir)


def _write_agent(base_dir: Path, name: str, config: dict, soul: str = "You are helpful.") -> None:
    """Write an agent directory with config.yaml and SOUL.md."""
    agent_dir = base_dir / "agents" / name
    agent_dir.mkdir(parents=True, exist_ok=True)

    config_copy = dict(config)
    if "name" not in config_copy:
        config_copy["name"] = name

    with open(agent_dir / "config.yaml", "w") as f:
        yaml.dump(config_copy, f)

    (agent_dir / "SOUL.md").write_text(soul, encoding="utf-8")


def _write_team_agent(
    base_dir: Path,
    name: str,
    config: dict,
    soul: str = "You are helpful.",
) -> None:
    """Write an enterprise team-agent directory."""
    agent_dir = base_dir / "team_agents" / name
    agent_dir.mkdir(parents=True, exist_ok=True)

    config_copy = dict(config)
    config_copy.setdefault("name", name)
    with open(agent_dir / "config.yaml", "w", encoding="utf-8") as file:
        yaml.safe_dump(config_copy, file, allow_unicode=True)
    (agent_dir / "SOUL.md").write_text(soul, encoding="utf-8")


# ===========================================================================
# 1. Paths class – agent path methods
# ===========================================================================


class TestPaths:
    def test_agents_dir(self, tmp_path):
        paths = _make_paths(tmp_path)
        assert paths.agents_dir == tmp_path / "agents"

    def test_agent_dir(self, tmp_path):
        paths = _make_paths(tmp_path)
        assert paths.agent_dir("code-reviewer") == tmp_path / "agents" / "code-reviewer"

    def test_team_agent_dir(self, tmp_path):
        paths = _make_paths(tmp_path)
        assert paths.team_agents_dir == tmp_path / "team_agents"
        assert paths.team_agent_dir("code-reviewer") == tmp_path / "team_agents" / "code-reviewer"

    def test_agent_memory_file(self, tmp_path):
        paths = _make_paths(tmp_path)
        assert paths.agent_memory_file("code-reviewer") == tmp_path / "agents" / "code-reviewer" / "memory.json"

    def test_user_md_file(self, tmp_path):
        paths = _make_paths(tmp_path)
        assert paths.user_md_file == tmp_path / "USER.md"

    def test_paths_are_different_from_global(self, tmp_path):
        paths = _make_paths(tmp_path)
        assert paths.memory_file != paths.agent_memory_file("my-agent")
        assert paths.memory_file == tmp_path / "memory.json"
        assert paths.agent_memory_file("my-agent") == tmp_path / "agents" / "my-agent" / "memory.json"


# ===========================================================================
# 2. AgentConfig – Pydantic parsing
# ===========================================================================


class TestAgentConfig:
    def test_minimal_config(self):
        from deerflow.config.agents_config import AgentConfig

        cfg = AgentConfig(name="my-agent")
        assert cfg.name == "my-agent"
        assert cfg.description == ""
        assert cfg.model is None
        assert cfg.tool_groups is None

    def test_full_config(self):
        from deerflow.config.agents_config import AgentConfig

        cfg = AgentConfig(
            name="code-reviewer",
            description="Specialized for code review",
            model="deepseek-v3",
            tool_groups=["file:read", "bash"],
        )
        assert cfg.name == "code-reviewer"
        assert cfg.model == "deepseek-v3"
        assert cfg.tool_groups == ["file:read", "bash"]

    def test_digital_employee_metadata(self):
        from deerflow.config.agents_config import AgentConfig

        cfg = AgentConfig(
            name="monthly-report",
            display_name="月报数字员工",
            category_id="research",
            tags=["月报", "研究"],
            avatar_id="report-research",
        )

        assert cfg.display_name == "月报数字员工"
        assert cfg.category_id == "research"
        assert cfg.tags == ["月报", "研究"]
        assert cfg.avatar_id == "report-research"

    def test_display_name_rejects_invisible_format_controls(self):
        from deerflow.config.agents_config import AgentConfig

        with pytest.raises(ValueError, match="invisible formatting controls"):
            AgentConfig(name="safe-id", display_name="安全\u202e名称")

    def test_invalid_legacy_display_name_is_ignored_without_hiding_employee(self):
        from deerflow.persistence.agents.base import parse_agent_config

        cfg = parse_agent_config(
            {
                "name": "safe-id",
                "display_name": "安全\u202e名称",
                "description": "仍应可访问",
            },
            "safe-id",
        )

        assert cfg.display_name is None
        assert cfg.description == "仍应可访问"

    def test_config_from_dict(self):
        from deerflow.config.agents_config import AgentConfig

        data = {"name": "test-agent", "description": "A test", "model": "gpt-4"}
        cfg = AgentConfig(**data)
        assert cfg.name == "test-agent"
        assert cfg.model == "gpt-4"
        assert cfg.tool_groups is None

    def test_agent_name_accepts_chinese_and_rejects_unsafe_characters(self):
        from deerflow.config.agents_config import validate_agent_name

        assert validate_agent_name("月报助手-2026") == "月报助手-2026"
        for invalid_name in ("月报 助手", "月报_助手", "../月报助手", "月报/助手"):
            with pytest.raises(ValueError, match="Invalid agent name"):
                validate_agent_name(invalid_name)


# ===========================================================================
# 3. load_agent_config
# ===========================================================================


class TestLoadAgentConfig:
    def test_load_valid_config(self, tmp_path):
        config_dict = {"name": "code-reviewer", "description": "Code review agent", "model": "deepseek-v3"}
        _write_agent(tmp_path, "code-reviewer", config_dict)

        with patch("deerflow.config.agents_config.get_paths", return_value=_make_paths(tmp_path)):
            from deerflow.config.agents_config import load_agent_config

            cfg = load_agent_config("code-reviewer")

        assert cfg.name == "code-reviewer"
        assert cfg.description == "Code review agent"
        assert cfg.model == "deepseek-v3"

    def test_load_missing_agent_raises(self, tmp_path):
        with patch("deerflow.config.agents_config.get_paths", return_value=_make_paths(tmp_path)):
            from deerflow.config.agents_config import load_agent_config

            with pytest.raises(FileNotFoundError):
                load_agent_config("nonexistent-agent")

    def test_load_missing_config_yaml_raises(self, tmp_path):
        # Create directory without config.yaml
        (tmp_path / "agents" / "broken-agent").mkdir(parents=True)

        with patch("deerflow.config.agents_config.get_paths", return_value=_make_paths(tmp_path)):
            from deerflow.config.agents_config import load_agent_config

            with pytest.raises(FileNotFoundError):
                load_agent_config("broken-agent")

    def test_load_config_infers_name_from_dir(self, tmp_path):
        """Config without 'name' field should use directory name."""
        agent_dir = tmp_path / "agents" / "inferred-name"
        agent_dir.mkdir(parents=True)
        (agent_dir / "config.yaml").write_text("description: My agent\n")
        (agent_dir / "SOUL.md").write_text("Hello")

        with patch("deerflow.config.agents_config.get_paths", return_value=_make_paths(tmp_path)):
            from deerflow.config.agents_config import load_agent_config

            cfg = load_agent_config("inferred-name")

        assert cfg.name == "inferred-name"

    def test_load_team_agent_config(self, tmp_path):
        _write_team_agent(
            tmp_path,
            "team-reviewer",
            {"description": "Shared review agent"},
        )

        with patch(
            "deerflow.config.agents_config.get_paths",
            return_value=_make_paths(tmp_path),
        ):
            from deerflow.config.agents_config import load_agent_config

            cfg = load_agent_config("team-reviewer")

        assert cfg is not None
        assert cfg.description == "Shared review agent"

    def test_load_config_with_tool_groups(self, tmp_path):
        config_dict = {"name": "restricted", "tool_groups": ["file:read", "file:write"]}
        _write_agent(tmp_path, "restricted", config_dict)

        with patch("deerflow.config.agents_config.get_paths", return_value=_make_paths(tmp_path)):
            from deerflow.config.agents_config import load_agent_config

            cfg = load_agent_config("restricted")

        assert cfg.tool_groups == ["file:read", "file:write"]

    def test_load_config_with_skills_empty_list(self, tmp_path):
        config_dict = {"name": "no-skills-agent", "skills": []}
        _write_agent(tmp_path, "no-skills-agent", config_dict)

        with patch("deerflow.config.agents_config.get_paths", return_value=_make_paths(tmp_path)):
            from deerflow.config.agents_config import load_agent_config

            cfg = load_agent_config("no-skills-agent")

        assert cfg.skills == []

    def test_load_config_with_skills_omitted(self, tmp_path):
        config_dict = {"name": "default-skills-agent"}
        _write_agent(tmp_path, "default-skills-agent", config_dict)

        with patch("deerflow.config.agents_config.get_paths", return_value=_make_paths(tmp_path)):
            from deerflow.config.agents_config import load_agent_config

            cfg = load_agent_config("default-skills-agent")

        assert cfg.skills is None

    def test_legacy_prompt_file_field_ignored(self, tmp_path):
        """Unknown fields like the old prompt_file should be silently ignored."""
        agent_dir = tmp_path / "agents" / "legacy-agent"
        agent_dir.mkdir(parents=True)
        (agent_dir / "config.yaml").write_text("name: legacy-agent\nprompt_file: system.md\n")
        (agent_dir / "SOUL.md").write_text("Soul content")

        with patch("deerflow.config.agents_config.get_paths", return_value=_make_paths(tmp_path)):
            from deerflow.config.agents_config import load_agent_config

            cfg = load_agent_config("legacy-agent")

        assert cfg.name == "legacy-agent"


# ===========================================================================
# 3b. resolve_agent_dir — memory-only directory fallback (#3390)
# ===========================================================================


class TestResolveAgentDirMemoryOnlyFallback:
    """Regression tests for #3390.

    When memory is enabled, the first conversation creates a user-isolated
    agent directory containing only ``memory.json`` (no ``config.yaml``).
    On the next turn ``resolve_agent_dir`` must fall through to the legacy
    shared layout instead of returning the incomplete user directory.
    """

    def test_user_dir_with_only_memory_falls_back_to_legacy(self, tmp_path):
        """User dir has memory.json but no config.yaml → use legacy dir."""
        from deerflow.config.agents_config import resolve_agent_dir

        # Legacy agent with full config
        legacy_dir = tmp_path / "agents" / "my-agent"
        legacy_dir.mkdir(parents=True)
        (legacy_dir / "config.yaml").write_text("name: my-agent\n", encoding="utf-8")
        (legacy_dir / "SOUL.md").write_text("legacy soul", encoding="utf-8")

        # User dir created by memory write — no config.yaml
        user_dir = tmp_path / "users" / "u1" / "agents" / "my-agent"
        user_dir.mkdir(parents=True)
        (user_dir / "memory.json").write_text("{}", encoding="utf-8")

        with patch("deerflow.config.agents_config.get_paths", return_value=_make_paths(tmp_path)), patch("deerflow.config.agents_config.get_effective_user_id", return_value="u1"):
            result = resolve_agent_dir("my-agent", user_id="u1")

        assert result == legacy_dir

    def test_user_dir_with_config_takes_priority(self, tmp_path):
        """User dir with config.yaml should still win over legacy."""
        from deerflow.config.agents_config import resolve_agent_dir

        # Legacy
        legacy_dir = tmp_path / "agents" / "my-agent"
        legacy_dir.mkdir(parents=True)
        (legacy_dir / "config.yaml").write_text("name: my-agent\n", encoding="utf-8")

        # User dir with full config (migrated)
        user_dir = tmp_path / "users" / "u1" / "agents" / "my-agent"
        user_dir.mkdir(parents=True)
        (user_dir / "config.yaml").write_text("name: my-agent\nmodel: gpt-4\n", encoding="utf-8")
        (user_dir / "memory.json").write_text("{}", encoding="utf-8")

        with patch("deerflow.config.agents_config.get_paths", return_value=_make_paths(tmp_path)), patch("deerflow.config.agents_config.get_effective_user_id", return_value="u1"):
            result = resolve_agent_dir("my-agent", user_id="u1")

        assert result == user_dir

    def test_load_config_falls_back_when_user_dir_is_memory_only(self, tmp_path):
        """End-to-end: load_agent_config works when user dir only has memory.json."""
        config_dict = {"name": "my-agent", "description": "Legacy agent", "model": "deepseek-v3"}
        _write_agent(tmp_path, "my-agent", config_dict)

        # Simulate memory write creating user dir without config
        user_dir = tmp_path / "users" / "u1" / "agents" / "my-agent"
        user_dir.mkdir(parents=True)
        (user_dir / "memory.json").write_text("{}", encoding="utf-8")

        with patch("deerflow.config.agents_config.get_paths", return_value=_make_paths(tmp_path)), patch("deerflow.config.agents_config.get_effective_user_id", return_value="u1"):
            from deerflow.config.agents_config import load_agent_config

            cfg = load_agent_config("my-agent", user_id="u1")

        assert cfg.name == "my-agent"
        assert cfg.model == "deepseek-v3"


# ===========================================================================
# 4. load_agent_soul
# ===========================================================================


class TestLoadAgentSoul:
    def test_reads_soul_file(self, tmp_path):
        expected_soul = "You are a specialized code review expert."
        _write_agent(tmp_path, "code-reviewer", {"name": "code-reviewer"}, soul=expected_soul)

        with patch("deerflow.config.agents_config.get_paths", return_value=_make_paths(tmp_path)):
            from deerflow.config.agents_config import AgentConfig, load_agent_soul

            cfg = AgentConfig(name="code-reviewer")
            soul = load_agent_soul(cfg.name)

        assert soul == expected_soul

    def test_missing_soul_file_returns_none(self, tmp_path):
        agent_dir = tmp_path / "agents" / "no-soul"
        agent_dir.mkdir(parents=True)
        (agent_dir / "config.yaml").write_text("name: no-soul\n")
        # No SOUL.md created

        with patch("deerflow.config.agents_config.get_paths", return_value=_make_paths(tmp_path)):
            from deerflow.config.agents_config import AgentConfig, load_agent_soul

            cfg = AgentConfig(name="no-soul")
            soul = load_agent_soul(cfg.name)

        assert soul is None

    def test_empty_soul_file_returns_none(self, tmp_path):
        agent_dir = tmp_path / "agents" / "empty-soul"
        agent_dir.mkdir(parents=True)
        (agent_dir / "config.yaml").write_text("name: empty-soul\n")
        (agent_dir / "SOUL.md").write_text("   \n   ")

        with patch("deerflow.config.agents_config.get_paths", return_value=_make_paths(tmp_path)):
            from deerflow.config.agents_config import AgentConfig, load_agent_soul

            cfg = AgentConfig(name="empty-soul")
            soul = load_agent_soul(cfg.name)

        assert soul is None

    def test_loads_soul_without_config_yaml(self, tmp_path):
        """SOUL.md should load even when the agent dir has no config.yaml (#4135)."""
        agent_dir = tmp_path / "agents" / "soul-only"
        agent_dir.mkdir(parents=True)
        # Deliberately no config.yaml – the agent is configured externally
        (agent_dir / "SOUL.md").write_text("You are a brave agent.", encoding="utf-8")

        with patch("deerflow.config.agents_config.get_paths", return_value=_make_paths(tmp_path)), patch("deerflow.config.agents_config.get_effective_user_id", return_value="default"):
            from deerflow.config.agents_config import load_agent_soul

            soul = load_agent_soul("soul-only")

        assert soul == "You are a brave agent."

    def test_loads_soul_from_user_dir_without_config_yaml(self, tmp_path):
        """Fallback should find SOUL.md when resolver returns a default dir without it (#4135).

        Setup: per-user agent 'foo' exists as a memory-only directory
        (no config.yaml, no SOUL.md). Legacy agent 'foo' has SOUL.md but
        no config.yaml. resolve_agent_dir returns the per-user path as
        default (neither dir has config.yaml). The fallback then finds
        SOUL.md in the legacy directory.
        """
        # Per-user dir: memory-only (no config.yaml, no SOUL.md)
        user_dir = tmp_path / "users" / "test-user" / "agents" / "foo"
        user_dir.mkdir(parents=True)
        (user_dir / "memory.json").write_text("{}", encoding="utf-8")

        # Legacy dir: has SOUL.md but no config.yaml
        legacy_dir = tmp_path / "agents" / "foo"
        legacy_dir.mkdir(parents=True)
        (legacy_dir / "SOUL.md").write_text("You are a legacy agent.", encoding="utf-8")

        with patch("deerflow.config.agents_config.get_paths", return_value=_make_paths(tmp_path)), patch("deerflow.config.agents_config.get_effective_user_id", return_value="test-user"):
            from deerflow.config.agents_config import load_agent_soul

            soul = load_agent_soul("foo")

        assert soul == "You are a legacy agent."

    def test_soul_not_leaked_from_legacy_when_per_user_has_config(self, tmp_path):
        """Per-user agent with config.yaml but no SOUL.md should NOT fall back to legacy SOUL.md.

        This verifies the gated condition: fallback only fires when the
        resolved dir lacks config.yaml. A properly-resolved per-user agent
        that simply has no SOUL.md returns None, preserving the
        "per-user entries fully shadow legacy entries" invariant.
        """
        # Legacy dir: has SOUL.md
        legacy_dir = tmp_path / "agents" / "foo"
        legacy_dir.mkdir(parents=True)
        (legacy_dir / "config.yaml").write_text("name: foo\n")
        (legacy_dir / "SOUL.md").write_text("You are a legacy agent.", encoding="utf-8")

        # Per-user dir: has config.yaml (resolver returns this) but no SOUL.md
        user_dir = tmp_path / "users" / "test-user" / "agents" / "foo"
        user_dir.mkdir(parents=True)
        (user_dir / "config.yaml").write_text("name: foo\n")
        # No SOUL.md in per-user dir

        with patch("deerflow.config.agents_config.get_paths", return_value=_make_paths(tmp_path)), patch("deerflow.config.agents_config.get_effective_user_id", return_value="test-user"):
            from deerflow.config.agents_config import load_agent_soul

            soul = load_agent_soul("foo")

        assert soul is None


# ===========================================================================
# 5. list_custom_agents
# ===========================================================================


class TestListCustomAgents:
    def test_empty_when_no_agents_dir(self, tmp_path):
        with patch("deerflow.config.agents_config.get_paths", return_value=_make_paths(tmp_path)):
            from deerflow.config.agents_config import list_custom_agents

            agents = list_custom_agents()

        assert agents == []

    def test_discovers_multiple_agents(self, tmp_path):
        _write_agent(tmp_path, "agent-a", {"name": "agent-a"})
        _write_agent(tmp_path, "agent-b", {"name": "agent-b", "description": "B"})

        with patch("deerflow.config.agents_config.get_paths", return_value=_make_paths(tmp_path)):
            from deerflow.config.agents_config import list_custom_agents

            agents = list_custom_agents()

        names = [a.name for a in agents]
        assert "agent-a" in names
        assert "agent-b" in names

    def test_skips_dirs_without_config_yaml(self, tmp_path):
        # Valid agent
        _write_agent(tmp_path, "valid-agent", {"name": "valid-agent"})
        # Invalid dir (no config.yaml)
        (tmp_path / "agents" / "invalid-dir").mkdir(parents=True)

        with patch("deerflow.config.agents_config.get_paths", return_value=_make_paths(tmp_path)):
            from deerflow.config.agents_config import list_custom_agents

            agents = list_custom_agents()

        assert len(agents) == 1
        assert agents[0].name == "valid-agent"

    def test_skips_non_directory_entries(self, tmp_path):
        # Create the agents dir with a file (not a dir)
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir(parents=True)
        (agents_dir / "not-a-dir.txt").write_text("hello")
        _write_agent(tmp_path, "real-agent", {"name": "real-agent"})

        with patch("deerflow.config.agents_config.get_paths", return_value=_make_paths(tmp_path)):
            from deerflow.config.agents_config import list_custom_agents

            agents = list_custom_agents()

        assert len(agents) == 1
        assert agents[0].name == "real-agent"

    def test_returns_sorted_by_name(self, tmp_path):
        _write_agent(tmp_path, "z-agent", {"name": "z-agent"})
        _write_agent(tmp_path, "a-agent", {"name": "a-agent"})
        _write_agent(tmp_path, "m-agent", {"name": "m-agent"})

        with patch("deerflow.config.agents_config.get_paths", return_value=_make_paths(tmp_path)):
            from deerflow.config.agents_config import list_custom_agents

            agents = list_custom_agents()

        names = [a.name for a in agents]
        assert names == sorted(names)


# ===========================================================================
# 7. Memory isolation: _get_memory_file_path
# ===========================================================================


class TestMemoryFilePath:
    def test_global_memory_path(self, tmp_path, monkeypatch):
        """None agent_name should return global memory file."""
        from deerflow.agents.memory.backends.deermem.deermem.config import DeerMemConfig
        from deerflow.agents.memory.backends.deermem.deermem.core.storage import FileMemoryStorage

        monkeypatch.setenv("DEERMEM_DATA_DIR", str(tmp_path))
        storage = FileMemoryStorage(DeerMemConfig())
        path = storage._get_memory_file_path(None)
        assert path == tmp_path / "memory.json"

    def test_agent_memory_path(self, tmp_path, monkeypatch):
        """Providing agent_name should return per-agent memory file."""
        from deerflow.agents.memory.backends.deermem.deermem.config import DeerMemConfig
        from deerflow.agents.memory.backends.deermem.deermem.core.storage import FileMemoryStorage

        monkeypatch.setenv("DEERMEM_DATA_DIR", str(tmp_path))
        storage = FileMemoryStorage(DeerMemConfig())
        path = storage._get_memory_file_path("code-reviewer")
        assert path == tmp_path / "agents" / "code-reviewer" / "memory.json"

    def test_different_paths_for_different_agents(self, tmp_path, monkeypatch):
        from deerflow.agents.memory.backends.deermem.deermem.config import DeerMemConfig
        from deerflow.agents.memory.backends.deermem.deermem.core.storage import FileMemoryStorage

        monkeypatch.setenv("DEERMEM_DATA_DIR", str(tmp_path))
        storage = FileMemoryStorage(DeerMemConfig())
        path_global = storage._get_memory_file_path(None)
        path_a = storage._get_memory_file_path("agent-a")
        path_b = storage._get_memory_file_path("agent-b")

        assert path_global != path_a
        assert path_global != path_b
        assert path_a != path_b


# ===========================================================================
# 8. Gateway API – Agents endpoints
# ===========================================================================


def _make_test_app(tmp_path: Path):
    """Create a FastAPI app with the agents router, patching paths to tmp_path."""
    from _router_auth_helpers import make_authed_test_app

    from app.gateway.routers.agents import router

    app = make_authed_test_app()

    @app.middleware("http")
    async def session_source(request, call_next):
        request.state.auth_source = "session"
        return await call_next(request)

    app.include_router(router)
    return app


@pytest.fixture()
def agent_client(tmp_path):
    """TestClient with agents router, using tmp_path as base_dir."""
    import app.gateway.routers.agents as agents_router

    paths_instance = _make_paths(tmp_path)
    previous_config = AgentsApiConfig(**get_agents_api_config().model_dump())

    with patch("deerflow.config.agents_config.get_paths", return_value=paths_instance), patch.object(agents_router, "get_paths", return_value=paths_instance):
        set_agents_api_config(AgentsApiConfig(enabled=True))
        try:
            app = _make_test_app(tmp_path)
            with TestClient(app) as client:
                client._tmp_path = tmp_path  # type: ignore[attr-defined]
                yield client
        finally:
            set_agents_api_config(previous_config)


@pytest.fixture()
def disabled_agent_client(tmp_path):
    """TestClient with agents router while the management API is disabled."""
    import app.gateway.routers.agents as agents_router

    paths_instance = _make_paths(tmp_path)
    previous_config = AgentsApiConfig(**get_agents_api_config().model_dump())

    with patch("deerflow.config.agents_config.get_paths", return_value=paths_instance), patch.object(agents_router, "get_paths", return_value=paths_instance):
        set_agents_api_config(AgentsApiConfig(enabled=False))
        try:
            app = _make_test_app(tmp_path)
            with TestClient(app) as client:
                yield client
        finally:
            set_agents_api_config(previous_config)


class TestAgentsAPI:
    @pytest.mark.parametrize(
        ("payload", "field"),
        [
            ({"name": "n" * 65}, "name"),
            ({"name": "description-limit", "description": "x" * 501}, "description"),
            ({"name": "tag-count-limit", "tags": [f"tag-{i}" for i in range(9)]}, "tags"),
            ({"name": "tag-length-limit", "tags": ["x" * 21]}, "tags"),
            ({"name": "unknown-category", "category_id": "not-configured"}, "category_id"),
        ],
    )
    def test_create_enforces_digital_employee_field_limits(self, agent_client, payload, field):
        response = agent_client.post(
            "/api/agents",
            json={**payload, "soul": "Helpful."},
        )
        assert response.status_code == 422, (field, response.text)

    def test_create_accepts_last_bundled_avatar(self, agent_client):
        response = agent_client.post(
            "/api/agents",
            json={
                "name": "renewables-specialist",
                "avatar_id": "renewables-engineer",
                "soul": "Helpful.",
            },
        )
        assert response.status_code == 201
        assert response.json()["avatar_id"] == "renewables-engineer"

    def test_create_rejects_invisible_display_name(self, agent_client):
        response = agent_client.post(
            "/api/agents",
            json={
                "name": "safe-id",
                "display_name": "安全\u202e名称",
                "soul": "Helpful.",
            },
        )
        assert response.status_code == 422

    def test_create_and_update_digital_employee_metadata(self, agent_client, tmp_path):
        response = agent_client.post(
            "/api/agents",
            json={
                "name": "monthly-report",
                "display_name": "月报数字员工",
                "description": "编写月报",
                "category_id": "science-technology-innovation",
                "tags": ["月报", "研究"],
                "avatar_id": "report-research",
                "soul": "Write concise reports.",
            },
        )

        assert response.status_code == 201
        created = response.json()
        assert created["display_name"] == "月报数字员工"
        assert created["category_id"] == "science-technology-innovation"
        assert created["tags"] == ["月报", "研究"]
        assert created["avatar_id"] == "report-research"

        updated = agent_client.put(
            "/api/agents/monthly-report",
            json={"category_id": "oil-gas-resource-development", "tags": ["日报", "自动化"]},
        )
        assert updated.status_code == 200
        assert updated.json()["category_id"] == "oil-gas-resource-development"
        assert updated.json()["tags"] == ["日报", "自动化"]

        config_file = tmp_path / "users" / "test-user-autouse" / "agents" / "monthly-report" / "config.yaml"
        config = yaml.safe_load(config_file.read_text(encoding="utf-8"))
        assert config["avatar_id"] == "report-research"

    def test_digital_employee_categories_use_fixed_corporate_domains(self, agent_client):
        response = agent_client.get("/api/digital-employee-categories")
        assert response.status_code == 200
        categories = response.json()["items"]
        assert len(categories) == 26
        assert [item["name"] for item in categories if item["domain_group"] == "unified"] == [
            "综合管理",
            "人力资源",
            "党建",
            "战略发展",
            "生产经营",
            "财务资本",
            "勘探开发",
            "工程技术",
            "科技创新",
            "安全环保",
            "数智化",
            "国际业务",
            "企业管理",
            "法务",
            "物资供应链",
            "销售贸易",
            "审计巡视",
        ]
        assert [item["name"] for item in categories if item["domain_group"] == "distributed"] == [
            "油气资源开发",
            "新能源",
            "油田服务",
            "海洋工程建设",
            "能源技术服务",
            "天然气与发电",
            "炼油化工",
            "化学",
            "金融服务",
        ]

    def test_digital_employee_category_admin_mutation_route_is_removed(self, agent_client):
        response = agent_client.put(
            "/api/admin/digital-employee-categories",
            json={"items": []},
        )
        assert response.status_code == 404

    def test_legacy_category_is_inferred_in_agent_response(self, agent_client, tmp_path):
        agent_dir = tmp_path / "users" / "test-user-autouse" / "agents" / "hse-reviewer"
        agent_dir.mkdir(parents=True)
        (agent_dir / "config.yaml").write_text(
            yaml.safe_dump(
                {
                    "name": "hse-reviewer",
                    "display_name": "HSE 安全审查员",
                    "category_id": "general",
                },
                allow_unicode=True,
            ),
            encoding="utf-8",
        )

        response = agent_client.get("/api/agents")

        assert response.status_code == 200
        assert response.json()["agents"][0]["category_id"] == "safety-environment"

    def test_file_category_migration_updates_personal_and_square_agents(self, agent_client, tmp_path):
        import app.gateway.routers.agents as agents_router

        personal_dir = tmp_path / "users" / "test-user-autouse" / "agents" / "hse-reviewer"
        personal_dir.mkdir(parents=True)
        (personal_dir / "config.yaml").write_text(
            yaml.safe_dump(
                {
                    "name": "hse-reviewer",
                    "display_name": "HSE 安全审查员",
                    "category_id": "general",
                },
                allow_unicode=True,
            ),
            encoding="utf-8",
        )
        team_dir = tmp_path / "team_agents" / "research-writer"
        team_dir.mkdir(parents=True)
        (team_dir / "config.yaml").write_text(
            yaml.safe_dump(
                {
                    "name": "research-writer",
                    "description": "科研报告编写",
                    "category_id": "research",
                },
                allow_unicode=True,
            ),
            encoding="utf-8",
        )
        app_config = MagicMock()
        app_config.agent_storage.backend = "file"

        with patch.object(agents_router, "get_app_config", return_value=app_config):
            migrated = agents_router.migrate_digital_employee_categories()

        assert migrated == 2
        personal = yaml.safe_load((personal_dir / "config.yaml").read_text(encoding="utf-8"))
        team = yaml.safe_load((team_dir / "config.yaml").read_text(encoding="utf-8"))
        assert personal["category_id"] == "safety-environment"
        assert team["category_id"] == "science-technology-innovation"

    def test_db_category_migration_uses_raw_field_patch(self, agent_client):
        import app.gateway.routers.agents as agents_router
        from deerflow.config.agents_config import AgentConfig

        app_config = MagicMock()
        app_config.agent_storage.backend = "db"
        store = MagicMock()
        store.list_all.return_value = [
            (
                "finance-user",
                AgentConfig(
                    name="settlement-reviewer",
                    display_name="财务决算报告复核员工",
                    category_id="general",
                ),
            )
        ]

        with (
            patch.object(agents_router, "get_app_config", return_value=app_config),
            patch.object(agents_router, "get_agent_store", return_value=store),
        ):
            migrated = agents_router.migrate_digital_employee_categories()

        assert migrated == 1
        store.patch_config.assert_called_once_with(
            "settlement-reviewer",
            {"category_id": "finance-capital"},
            user_id="finance-user",
        )
        store.update.assert_not_called()

    def test_custom_avatar_is_reencoded_and_served(self, agent_client):
        created = agent_client.post("/api/agents", json={"name": "avatar-agent", "soul": "Helpful."})
        assert created.status_code == 201

        png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=")
        uploaded = agent_client.post(
            "/api/agents/avatar-agent/avatar",
            files={"file": ("avatar.png", png, "image/png")},
        )

        assert uploaded.status_code == 200
        assert uploaded.json()["avatar_url"].endswith("/api/agents/avatar-agent/avatar")
        served = agent_client.get("/api/agents/avatar-agent/avatar")
        assert served.status_code == 200
        assert served.headers["content-type"].startswith("image/webp")
        assert served.content.startswith(b"RIFF")

    def test_external_agent_click_accepts_builtin_agent(self, agent_client):
        response = agent_client.post(
            "/api/agent-analytics/external-click",
            json={"agent_name": "专业文档解析专家"},
        )
        assert response.status_code == 204

    def test_external_agent_click_rejects_unknown_agent(self, agent_client):
        response = agent_client.post(
            "/api/agent-analytics/external-click",
            json={"agent_name": "unknown"},
        )
        assert response.status_code == 422

    def test_external_platform_click_requires_configured_key(self, agent_client, monkeypatch):
        monkeypatch.delenv("EXTERNAL_AGENT_CLICK_INGEST_KEY", raising=False)
        response = agent_client.post(
            "/api/agent_click",
            json={
                "user_name": "测试用户",
                "user_email": "user@example.com",
                "agent_name": "勘探开发图文智能检索工具",
                "source": "海能平台",
            },
        )
        assert response.status_code == 503

    def test_external_platform_click_normalizes_and_persists(self, agent_client, monkeypatch):
        import app.gateway.routers.agents as agents_router

        class FakeSession:
            def __init__(self):
                self.row = None
                self.committed = False

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

            def add(self, row):
                self.row = row

            async def commit(self):
                self.committed = True

        session = FakeSession()
        monkeypatch.setenv("EXTERNAL_AGENT_CLICK_INGEST_KEY", "test-ingest-key")
        monkeypatch.setattr(agents_router, "get_session_factory", lambda: lambda: session)

        response = agent_client.post(
            "/api/agent_click",
            headers={"Authorization": "Bearer test-ingest-key"},
            json={
                "user_name": "测试用户",
                "user_email": "USER@EXAMPLE.COM",
                "agent_name": "勘探开发图文智能检索工具",
                "source": "协同平台",
            },
        )

        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
        assert session.committed is True
        assert session.row.action == "external_agent.remote_click"
        assert session.row.resource_id == "专业文档解析专家"
        assert session.row.user_email == "user@example.com"
        assert session.row.details_json["source"] == "协同平台"

    def test_external_platform_click_rejects_invalid_key(self, agent_client, monkeypatch):
        monkeypatch.setenv("EXTERNAL_AGENT_CLICK_INGEST_KEY", "test-ingest-key")
        response = agent_client.post(
            "/api/agent_click",
            headers={"X-External-Agent-Click-Key": "wrong"},
            json={
                "user_name": "测试用户",
                "agent_name": "自动数采专家",
                "source": "协同平台",
            },
        )
        assert response.status_code == 401

    def test_list_agents_empty(self, agent_client):
        response = agent_client.get("/api/agents")
        assert response.status_code == 200
        data = response.json()
        assert data["agents"] == []

    def test_create_agent(self, agent_client):
        payload = {
            "name": "code-reviewer",
            "description": "Reviews code",
            "soul": "You are a code reviewer.",
        }
        response = agent_client.post("/api/agents", json=payload)
        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "code-reviewer"
        assert data["description"] == "Reviews code"
        assert data["soul"] == "You are a code reviewer."

    def test_create_agent_with_chinese_name(self, agent_client):
        payload = {
            "name": "月报助手-2026",
            "description": "生成业务月报",
            "soul": "你是月报助手。",
        }
        response = agent_client.post("/api/agents", json=payload)

        assert response.status_code == 201
        assert response.json()["name"] == "月报助手-2026"

    def test_create_agent_invalid_name(self, agent_client):
        payload = {"name": "Code Reviewer!", "soul": "test"}
        response = agent_client.post("/api/agents", json=payload)
        assert response.status_code == 422

    def test_create_duplicate_agent_409(self, agent_client):
        payload = {"name": "my-agent", "soul": "test"}
        agent_client.post("/api/agents", json=payload)

        # Second create should fail
        response = agent_client.post("/api/agents", json=payload)
        assert response.status_code == 409

    def test_list_agents_after_create(self, agent_client):
        agent_client.post("/api/agents", json={"name": "agent-one", "soul": "p1"})
        agent_client.post("/api/agents", json={"name": "agent-two", "soul": "p2"})

        response = agent_client.get("/api/agents")
        assert response.status_code == 200
        names = [a["name"] for a in response.json()["agents"]]
        assert "agent-one" in names
        assert "agent-two" in names

    def test_list_agents_includes_soul(self, agent_client):
        agent_client.post("/api/agents", json={"name": "soul-agent", "soul": "My soul content"})

        response = agent_client.get("/api/agents")
        assert response.status_code == 200
        agents = response.json()["agents"]
        soul_agent = next(a for a in agents if a["name"] == "soul-agent")
        assert soul_agent["soul"] == "My soul content"

    def test_get_agent(self, agent_client):
        agent_client.post("/api/agents", json={"name": "test-agent", "soul": "Hello world"})

        response = agent_client.get("/api/agents/test-agent")
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "test-agent"
        assert data["soul"] == "Hello world"

    def test_get_missing_agent_404(self, agent_client):
        response = agent_client.get("/api/agents/nonexistent")
        assert response.status_code == 404

    def test_update_agent_soul(self, agent_client):
        agent_client.post("/api/agents", json={"name": "update-me", "soul": "original"})

        response = agent_client.put("/api/agents/update-me", json={"soul": "updated"})
        assert response.status_code == 200
        assert response.json()["soul"] == "updated"

    def test_update_agent_description(self, agent_client):
        agent_client.post("/api/agents", json={"name": "desc-agent", "description": "old desc", "soul": "p"})

        response = agent_client.put("/api/agents/desc-agent", json={"description": "new desc"})
        assert response.status_code == 200
        assert response.json()["description"] == "new desc"

    def test_update_agent_preserves_hand_authored_github_block(self, agent_client):
        """A hand-authored ``github:`` block on disk must survive PATCH.

        The HTTP route does not expose ``github`` as an editable field
        (and rightly so — the GitHub App credentials and binding triggers
        are operator-authored, not end-user-editable). But it MUST carry
        the block forward when rewriting ``config.yaml`` for a description /
        model / tool_groups / skills change, otherwise an operator who
        edits the agent's description from the Web UI silently strips the
        binding and the next webhook delivery silently no-ops.

        Mirrors the same property the harness ``update_agent`` tool enforces
        via ``preserve_non_managed_fields``; both surfaces share the helper.
        """
        # Create an agent through the API, then hand-author a github: block
        # into its config.yaml — exactly the workflow an operator would
        # follow when wiring a new repo binding.
        agent_client.post("/api/agents", json={"name": "github-agent", "description": "old desc", "soul": "p"})

        tmp_path: Path = agent_client._tmp_path  # type: ignore[attr-defined]
        agent_dir = tmp_path / "users" / "test-user-autouse" / "agents" / "github-agent"
        config_file = agent_dir / "config.yaml"
        config_data = yaml.safe_load(config_file.read_text())
        config_data["github"] = {
            "installation_id": 99999,
            "bot_login": "github-agent-bot",
            "bindings": [
                {
                    "repo": "acme/widget",
                    "triggers": {"pull_request": {"actions": ["opened"]}},
                }
            ],
        }
        config_file.write_text(yaml.safe_dump(config_data, sort_keys=False), encoding="utf-8")

        # PATCH only the description.
        response = agent_client.put("/api/agents/github-agent", json={"description": "new desc"})
        assert response.status_code == 200

        # github: block must survive verbatim.
        reloaded = yaml.safe_load(config_file.read_text())
        assert reloaded["description"] == "new desc"
        assert reloaded["github"] == {
            "installation_id": 99999,
            "bot_login": "github-agent-bot",
            "bindings": [
                {
                    "repo": "acme/widget",
                    "triggers": {"pull_request": {"actions": ["opened"]}},
                }
            ],
        }

    def test_update_memory_only_user_dir_with_legacy_agent_returns_409(self, agent_client, tmp_path):
        """Regression for #3390's PUT /api/agents/{name} guard.

        A per-user agent directory can exist containing only memory.json
        (written the first time this user chats with a legacy shared
        agent). The stale guard checked bare directory existence and
        missed this case, letting the route silently fork a brand-new
        config.yaml/SOUL.md into the memory-only directory instead of
        blocking with the migration-script guidance.
        """
        legacy_dir = tmp_path / "agents" / "legacy-agent"
        legacy_dir.mkdir(parents=True)
        (legacy_dir / "config.yaml").write_text("name: legacy-agent\ndescription: legacy\n", encoding="utf-8")
        (legacy_dir / "SOUL.md").write_text("legacy soul", encoding="utf-8")

        user_agent_dir = tmp_path / "users" / "test-user-autouse" / "agents" / "legacy-agent"
        user_agent_dir.mkdir(parents=True)
        (user_agent_dir / "memory.json").write_text("{}", encoding="utf-8")

        response = agent_client.put("/api/agents/legacy-agent", json={"soul": "should not write"})

        assert response.status_code == 409
        assert not (user_agent_dir / "config.yaml").exists()
        assert not (user_agent_dir / "SOUL.md").exists()
        assert (user_agent_dir / "memory.json").exists(), "the user's existing memory must be left untouched"

    def test_update_missing_agent_404(self, agent_client):
        response = agent_client.put("/api/agents/ghost-agent", json={"soul": "new"})
        assert response.status_code == 404

    def test_delete_agent(self, agent_client):
        agent_client.post("/api/agents", json={"name": "del-me", "soul": "bye"})

        import app.gateway.routers.agents as agents_router

        memory_manager = MagicMock()
        memory_manager.cancel_by_agent.return_value = 1
        with patch.object(agents_router, "get_memory_manager", return_value=memory_manager):
            response = agent_client.delete("/api/agents/del-me")
        assert response.status_code == 204
        assert memory_manager.cancel_by_agent.call_count == 2
        memory_manager.cancel_by_agent.assert_called_with("del-me", user_id="test-user-autouse")

        # Verify it's gone
        response = agent_client.get("/api/agents/del-me")
        assert response.status_code == 404

    def test_delete_missing_agent_404(self, agent_client):
        response = agent_client.delete("/api/agents/does-not-exist")
        assert response.status_code == 404

    def test_create_agent_with_model_and_tool_groups(self, agent_client):
        payload = {
            "name": "specialized",
            "description": "Specialized agent",
            "model": "test-model",
            "tool_groups": ["file:read", "bash"],
            "soul": "You are specialized.",
        }
        response = agent_client.post("/api/agents", json=payload)
        assert response.status_code == 201
        data = response.json()
        assert data["model"] == "test-model"
        assert data["tool_groups"] == ["file:read", "bash"]

    def test_create_persists_files_on_disk(self, agent_client, tmp_path):
        agent_client.post("/api/agents", json={"name": "disk-check", "soul": "disk soul"})

        # tests/conftest.py installs an autouse fixture that sets the
        # contextvar to "test-user-autouse", so the agent is persisted under
        # users/test-user-autouse/agents/ rather than the legacy shared dir.
        agent_dir = tmp_path / "users" / "test-user-autouse" / "agents" / "disk-check"
        assert agent_dir.exists()
        assert (agent_dir / "config.yaml").exists()
        assert (agent_dir / "SOUL.md").exists()
        assert (agent_dir / "SOUL.md").read_text() == "disk soul"

    def test_delete_removes_files_from_disk(self, agent_client, tmp_path):
        agent_client.post("/api/agents", json={"name": "remove-me", "soul": "bye"})
        agent_dir = tmp_path / "users" / "test-user-autouse" / "agents" / "remove-me"
        assert agent_dir.exists()

        agent_client.delete("/api/agents/remove-me")
        assert not agent_dir.exists()

    def test_create_rejects_legacy_name_collision(self, agent_client, tmp_path):
        """An unmigrated legacy agent must still block name collision so that
        running the migration script later won't shadow the legacy entry."""
        legacy_dir = tmp_path / "agents" / "legacy-agent"
        legacy_dir.mkdir(parents=True)
        (legacy_dir / "config.yaml").write_text("name: legacy-agent\n", encoding="utf-8")
        (legacy_dir / "SOUL.md").write_text("legacy soul", encoding="utf-8")

        response = agent_client.post("/api/agents", json={"name": "legacy-agent", "soul": "x"})
        assert response.status_code == 409

    def test_team_agents_are_listed_and_loaded(self, agent_client, tmp_path):
        _write_team_agent(
            tmp_path,
            "shared-reviewer",
            {"description": "A shared review agent"},
            soul="Review documents carefully.",
        )

        response = agent_client.get("/api/team-agents")

        assert response.status_code == 200
        agent = response.json()["agents"][0]
        assert agent["name"] == "shared-reviewer"
        assert agent["source"] == "team"
        assert agent["soul"] == "Review documents carefully."

    def test_publish_and_non_publisher_unpublish_is_denied(self, agent_client, tmp_path):
        agent_client.post(
            "/api/agents",
            json={
                "name": "publish-me",
                "description": "Publishable agent",
                "soul": "Shared soul",
            },
        )

        publish_response = agent_client.post(
            "/api/agents/publish-me/publish",
            json={"overwrite": True},
        )

        assert publish_response.status_code == 200
        published = publish_response.json()
        assert published["source"] == "team"
        assert published["published_by_current_user"] is True
        team_dir = tmp_path / "team_agents" / "publish-me"
        assert (team_dir / "config.yaml").exists()
        assert (team_dir / "SOUL.md").read_text(encoding="utf-8") == "Shared soul"

        config = yaml.safe_load((team_dir / "config.yaml").read_text(encoding="utf-8"))
        config["published_by_user_id"] = "another-publisher"
        (team_dir / "config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")

        unpublish_response = agent_client.delete("/api/team-agents/publish-me")
        assert unpublish_response.status_code == 403
        assert team_dir.exists()

    def test_personal_delete_does_not_remove_team_agent(self, agent_client, tmp_path):
        _write_team_agent(tmp_path, "team-only", {"description": "Shared"})

        response = agent_client.delete("/api/agents/team-only")

        assert response.status_code == 409
        assert (tmp_path / "team_agents" / "team-only" / "config.yaml").exists()


# ===========================================================================
# 9. Gateway API – User Profile endpoints
# ===========================================================================


class TestUserProfileAPI:
    def test_get_user_profile_empty(self, agent_client):
        response = agent_client.get("/api/user-profile")
        assert response.status_code == 200
        assert response.json()["content"] is None

    def test_put_user_profile(self, agent_client, tmp_path):
        content = "# User Profile\n\nI am a developer."
        response = agent_client.put("/api/user-profile", json={"content": content})
        assert response.status_code == 200
        assert response.json()["content"] == content

        # File should be written to disk
        user_md = tmp_path / "USER.md"
        assert user_md.exists()
        assert user_md.read_text(encoding="utf-8") == content

    def test_get_user_profile_after_put(self, agent_client):
        content = "# Profile\n\nI work on data science."
        agent_client.put("/api/user-profile", json={"content": content})

        response = agent_client.get("/api/user-profile")
        assert response.status_code == 200
        assert response.json()["content"] == content

    def test_put_empty_user_profile_returns_none(self, agent_client):
        response = agent_client.put("/api/user-profile", json={"content": ""})
        assert response.status_code == 200
        assert response.json()["content"] is None


class TestAgentsApiDisabled:
    def test_agents_list_returns_403(self, disabled_agent_client):
        response = disabled_agent_client.get("/api/agents")
        assert response.status_code == 403
        assert "agents_api.enabled=true" in response.json()["detail"]

    def test_agent_get_returns_403(self, disabled_agent_client):
        response = disabled_agent_client.get("/api/agents/example-agent")
        assert response.status_code == 403

    def test_agent_name_check_returns_403(self, disabled_agent_client):
        response = disabled_agent_client.get("/api/agents/check", params={"name": "example-agent"})
        assert response.status_code == 403

    def test_agent_create_returns_403(self, disabled_agent_client):
        response = disabled_agent_client.post("/api/agents", json={"name": "example-agent", "soul": "blocked"})
        assert response.status_code == 403

    def test_agent_update_returns_403(self, disabled_agent_client):
        response = disabled_agent_client.put("/api/agents/example-agent", json={"description": "blocked"})
        assert response.status_code == 403

    def test_agent_delete_returns_403(self, disabled_agent_client):
        response = disabled_agent_client.delete("/api/agents/example-agent")
        assert response.status_code == 403

    def test_user_profile_routes_return_403(self, disabled_agent_client):
        get_response = disabled_agent_client.get("/api/user-profile")
        put_response = disabled_agent_client.put("/api/user-profile", json={"content": "blocked"})

        assert get_response.status_code == 403
        assert put_response.status_code == 403
