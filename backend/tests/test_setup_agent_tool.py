"""Tests for setup_agent tool — validates agent name security and data loss prevention."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import yaml

from deerflow.tools.builtins.setup_agent_tool import setup_agent

# --- Helpers ---


class _DummyRuntime(SimpleNamespace):
    context: dict
    tool_call_id: str


def _make_runtime(agent_name: str | None = "test-agent") -> MagicMock:
    runtime = MagicMock()
    runtime.context = {"agent_name": agent_name}
    runtime.tool_call_id = "call_1"
    return runtime


def _make_paths_mock(tmp_path: Path):
    paths = MagicMock()
    paths.base_dir = tmp_path
    paths.agent_dir = lambda name: tmp_path / "agents" / name
    paths.user_agent_dir = lambda user_id, name: tmp_path / "users" / user_id / "agents" / name
    return paths


@contextmanager
def _use_file_agent_store(tmp_path: Path):
    """Keep file-store tests independent from the developer's config.yaml."""
    from deerflow.persistence.agents.file import FileAgentStore

    paths = _make_paths_mock(tmp_path)
    with (
        patch("deerflow.tools.builtins.setup_agent_tool.get_paths", return_value=paths),
        patch("deerflow.config.agents_config.get_paths", return_value=paths),
        patch(
            "deerflow.tools.builtins.setup_agent_tool.get_agent_store",
            return_value=FileAgentStore(),
        ),
    ):
        yield


def _call_setup_agent(tmp_path: Path, soul: str, description: str, agent_name: str = "test-agent"):
    """Call the underlying setup_agent function directly, bypassing langchain tool wrapper."""
    with _use_file_agent_store(tmp_path):
        return setup_agent.func(
            soul=soul,
            description=description,
            runtime=_make_runtime(agent_name),
        )


# --- Agent name validation tests ---


def test_setup_agent_rejects_invalid_agent_name_before_writing(tmp_path, monkeypatch):
    monkeypatch.setenv("DEER_FLOW_HOME", str(tmp_path))
    outside_dir = tmp_path.parent / "outside-target"
    traversal_agent = f"../../../{outside_dir.name}/evil"
    runtime = _DummyRuntime(context={"agent_name": traversal_agent}, tool_call_id="tool-1")

    result = setup_agent.func(soul="test soul", description="desc", runtime=runtime)

    messages = result.update["messages"]
    assert len(messages) == 1
    assert "Invalid agent name" in messages[0].content
    assert not (tmp_path / "users" / "test-user-autouse" / "agents").exists()
    assert not (outside_dir / "evil" / "SOUL.md").exists()


def test_setup_agent_rejects_absolute_agent_name_before_writing(tmp_path, monkeypatch):
    monkeypatch.setenv("DEER_FLOW_HOME", str(tmp_path))
    absolute_agent = str(tmp_path / "outside-agent")
    runtime = _DummyRuntime(context={"agent_name": absolute_agent}, tool_call_id="tool-2")

    result = setup_agent.func(soul="test soul", description="desc", runtime=runtime)

    messages = result.update["messages"]
    assert len(messages) == 1
    assert "Invalid agent name" in messages[0].content
    assert not (tmp_path / "users" / "test-user-autouse" / "agents").exists()
    assert not (Path(absolute_agent) / "SOUL.md").exists()


# --- Data loss prevention tests ---


class TestSetupAgentNoDataLoss:
    """Ensure shutil.rmtree only removes directories created during the current call."""

    def test_existing_agent_dir_preserved_on_failure(self, tmp_path: Path):
        """If the agent directory already exists and setup fails,
        the directory and its contents must NOT be deleted."""
        agent_dir = tmp_path / "users" / "test-user-autouse" / "agents" / "test-agent"
        agent_dir.mkdir(parents=True)
        old_soul = agent_dir / "SOUL.md"
        old_soul.write_text("original soul content", encoding="utf-8")

        with _use_file_agent_store(tmp_path):
            # Force soul_file.write_text to raise after directory already exists
            with patch.object(Path, "write_text", side_effect=OSError("disk full")):
                setup_agent.func(
                    soul="new soul",
                    description="desc",
                    runtime=_make_runtime(),
                )

        # Directory must still exist
        assert agent_dir.exists(), "Pre-existing agent directory was deleted on failure"
        # Original SOUL.md should still be on disk (not deleted by rmtree)
        assert old_soul.exists(), "Pre-existing SOUL.md was deleted on failure"

    def test_new_agent_dir_cleaned_up_on_failure(self, tmp_path: Path):
        """If the agent directory is newly created and setup fails,
        the directory should be cleaned up."""
        agent_dir = tmp_path / "users" / "test-user-autouse" / "agents" / "test-agent"
        assert not agent_dir.exists()

        with _use_file_agent_store(tmp_path):
            with patch("deerflow.persistence.agents.drafts._replace_bytes", side_effect=OSError("write error")):
                setup_agent.func(
                    soul="new soul",
                    description="desc",
                    runtime=_make_runtime(),
                )

        # Newly created directory should be cleaned up
        assert not agent_dir.exists(), "Newly created agent directory was not cleaned up on failure"

    def test_successful_setup_creates_files(self, tmp_path: Path):
        """Happy path: setup_agent creates config.yaml and SOUL.md."""
        _call_setup_agent(tmp_path, soul="# My Agent", description="A test agent")

        agent_dir = tmp_path / "users" / "test-user-autouse" / "agents" / "test-agent"
        assert agent_dir.exists()
        assert (agent_dir / "SOUL.md").read_text() == "# My Agent"
        assert (agent_dir / "config.yaml").exists()

    def test_bootstrap_preserves_existing_creator_configuration(self, tmp_path):
        agent_dir = tmp_path / "users" / "test-user-autouse" / "agents" / "test-agent"
        agent_dir.mkdir(parents=True)
        existing = {
            "name": "test-agent",
            "display_name": "地质分析员",
            "skills": [],
            "model": "selected-model",
            "category_id": "oil-gas-resource-development",
            "tags": ["地质"],
            "thinking_enabled": False,
        }
        (agent_dir / "config.yaml").write_text(yaml.safe_dump(existing, allow_unicode=True), encoding="utf-8")
        result = _call_setup_agent(tmp_path, soul="new soul", description="new description")
        assert result.update.get("created_agent_name") == "test-agent"
        saved = yaml.safe_load((agent_dir / "config.yaml").read_text(encoding="utf-8"))
        for key, value in existing.items():
            assert saved[key] == value

    def test_bootstrap_rejects_other_users_skill_before_writing(self, tmp_path):
        with _use_file_agent_store(tmp_path), patch("deerflow.skills.agent_selection.get_or_new_user_skill_storage") as storage:
            storage.return_value.load_skills.return_value = []
            result = setup_agent.func(soul="new soul", description="desc", skills=["someone-elses-skill"], runtime=_make_runtime())
        assert result.update["messages"][0].status == "error"
        assert "created_agent_name" not in result.update
        assert not (tmp_path / "users" / "test-user-autouse" / "agents" / "test-agent").exists()

    @pytest.mark.no_auto_user
    def test_runtime_user_id_used_when_contextvar_missing(self, tmp_path: Path):
        """setup_agent should not fall back to default when runtime carries user_id."""
        runtime = _DummyRuntime(
            context={"agent_name": "test-agent", "user_id": "auth-user-42"},
            tool_call_id="tool-3",
        )

        with _use_file_agent_store(tmp_path):
            setup_agent.func(
                soul="# My Agent",
                description="A test agent",
                runtime=runtime,
            )

        expected_dir = tmp_path / "users" / "auth-user-42" / "agents" / "test-agent"
        default_dir = tmp_path / "users" / "default" / "agents" / "test-agent"
        assert (expected_dir / "SOUL.md").read_text() == "# My Agent"
        assert not default_dir.exists()


# --- Empty soul guard tests  ---


@pytest.mark.parametrize("selection", [[], None])
def test_builder_form_selection_takes_precedence_over_model_suggestion(tmp_path, selection):
    runtime = _make_runtime()
    runtime.context.update({"is_bootstrap": True, "agent_skill_selection": selection})
    with _use_file_agent_store(tmp_path):
        result = setup_agent.func(soul="new soul", description="desc", skills=["model-invented"], runtime=runtime)
    assert result.update.get("created_agent_name") == "test-agent"
    config_file = tmp_path / "users" / "test-user-autouse" / "agents" / "test-agent" / "config.yaml"
    assert yaml.safe_load(config_file.read_text())["skills"] == selection


def test_builder_form_selection_still_requires_current_user_access(tmp_path):
    runtime = _make_runtime()
    runtime.context.update({"is_bootstrap": True, "agent_skill_selection": ["another-user-private"]})
    with _use_file_agent_store(tmp_path), patch("deerflow.skills.agent_selection.get_or_new_user_skill_storage") as storage:
        storage.return_value.load_skills.return_value = []
        result = setup_agent.func(soul="new soul", description="desc", runtime=runtime)
    assert result.update["messages"][0].status == "error"
    assert "created_agent_name" not in result.update


@pytest.mark.parametrize("selection", ["analysis", {"name": "analysis"}, [None], [" analysis"]])
def test_builder_rejects_malformed_form_selection(tmp_path, selection):
    runtime = _make_runtime()
    runtime.context.update({"is_bootstrap": True, "agent_skill_selection": selection})
    with _use_file_agent_store(tmp_path):
        result = setup_agent.func(soul="new soul", description="desc", runtime=runtime)
    assert result.update["messages"][0].status == "error"
    assert "created_agent_name" not in result.update


class TestSetupAgentEmptySoulGuard:
    """The tool must refuse to persist an empty / whitespace-only SOUL.md and
    must not touch the filesystem at all, so an existing SOUL.md (per-agent or
    global default) cannot be silently overwritten with empty content.
    """

    def test_empty_soul_returns_error_and_does_not_write(self, tmp_path: Path):
        result = _call_setup_agent(tmp_path, soul="", description="desc")

        messages = result.update["messages"]
        assert len(messages) == 1
        assert "soul content is empty" in messages[0].content
        assert "created_agent_name" not in result.update
        agent_dir = tmp_path / "users" / "test-user-autouse" / "agents" / "test-agent"
        assert not agent_dir.exists()

    def test_whitespace_only_soul_returns_error_and_does_not_write(self, tmp_path: Path):
        result = _call_setup_agent(tmp_path, soul="   \n\t  ", description="desc")

        messages = result.update["messages"]
        assert len(messages) == 1
        assert "soul content is empty" in messages[0].content
        agent_dir = tmp_path / "users" / "test-user-autouse" / "agents" / "test-agent"
        assert not agent_dir.exists()

    def test_empty_soul_does_not_overwrite_existing_global_soul(self, tmp_path: Path):
        """If agent_name resolution would have fallen back to base_dir, an
        empty soul must not clobber a pre-existing global SOUL.md.
        """
        global_soul = tmp_path / "SOUL.md"
        global_soul.write_text("original global soul", encoding="utf-8")

        with _use_file_agent_store(tmp_path):
            setup_agent.func(
                soul="",
                description="desc",
                runtime=_DummyRuntime(context={"agent_name": None}, tool_call_id="tool-empty"),
            )

        assert global_soul.read_text(encoding="utf-8") == "original global soul"

    def test_empty_soul_does_not_overwrite_existing_per_agent_soul(self, tmp_path: Path):
        agent_dir = tmp_path / "users" / "test-user-autouse" / "agents" / "test-agent"
        agent_dir.mkdir(parents=True)
        existing_soul = agent_dir / "SOUL.md"
        existing_soul.write_text("original per-agent soul", encoding="utf-8")

        _call_setup_agent(tmp_path, soul="   ", description="desc")

        assert existing_soul.read_text(encoding="utf-8") == "original per-agent soul"
