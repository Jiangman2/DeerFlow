"""Square selection must not silently resolve a same-name personal Agent."""

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from deerflow.config import agents_config as ac
from deerflow.config.paths import Paths
from deerflow.persistence.agents.file import FileAgentStore


@pytest.fixture
def catalog(tmp_path, monkeypatch):
    paths = Paths(base_dir=tmp_path)
    monkeypatch.setattr(ac, "get_paths", lambda: paths)
    monkeypatch.setattr("deerflow.persistence.agents.get_agent_store", lambda: FileAgentStore())
    for source, directory in (
        ("personal", paths.user_agent_dir("viewer", "reports")),
        ("team", paths.team_agent_dir("reports")),
        ("legacy", paths.agent_dir("reports")),
    ):
        directory.mkdir(parents=True)
        (directory / "config.yaml").write_text(yaml.safe_dump({"name": "reports", "description": source}), encoding="utf-8")
        (directory / "SOUL.md").write_text(source + " soul", encoding="utf-8")
    return paths


@pytest.mark.parametrize("source", ["personal", "team", "legacy"])
def test_explicit_source_selects_matching_config_and_soul(catalog, source):
    assert ac.load_agent_config("reports", user_id="viewer", source=source).description == source
    assert ac.load_agent_soul("reports", user_id="viewer", source=source) == source + " soul"


@pytest.mark.parametrize("source", ["personal", "team", "legacy"])
def test_missing_explicit_source_never_falls_back(catalog, source):
    directory: Path = {
        "personal": catalog.user_agent_dir("viewer", "reports"),
        "team": catalog.team_agent_dir("reports"),
        "legacy": catalog.agent_dir("reports"),
    }[source]
    (directory / "config.yaml").unlink()
    with pytest.raises(FileNotFoundError):
        ac.load_agent_config("reports", user_id="viewer", source=source)
    with pytest.raises(FileNotFoundError):
        ac.load_agent_soul("reports", user_id="viewer", source=source)


def test_personal_source_cannot_read_another_user_or_shared(catalog):
    with pytest.raises(FileNotFoundError):
        ac.load_agent_config("reports", user_id="other", source="personal")


def test_omitted_source_keeps_legacy_resolution(catalog):
    assert ac.load_agent_config("reports", user_id="viewer").description == "personal"
    assert ac.load_agent_config("reports", user_id="other").description == "team"


@pytest.mark.parametrize("source", ["all", "../team", "", {}, False])
def test_unrecognized_source_fails_closed(catalog, source):
    with pytest.raises(ValueError, match="source"):
        ac.load_agent_config("reports", user_id="viewer", source=source)
    with pytest.raises(ValueError, match="source"):
        ac.load_agent_soul("reports", user_id="viewer", source=source)


@pytest.mark.parametrize("source", ["team", "legacy"])
def test_shared_context_tools_cannot_modify_same_name_personal_agent(catalog, source):
    from deerflow.tools.builtins.setup_agent_tool import setup_agent
    from deerflow.tools.builtins.update_agent_tool import update_agent

    runtime = SimpleNamespace(context={"agent_name": "reports", "agent_source": source, "user_id": "viewer"}, tool_call_id="call-source")
    for tool in (setup_agent, update_agent):
        result = tool.func(soul="unexpected change", description="changed", runtime=runtime)
        assert "Error:" in result.update["messages"][0].content
    assert ac.load_agent_soul("reports", user_id="viewer", source="personal") == "personal soul"
    assert ac.load_agent_soul("reports", user_id="viewer", source=source) == source + " soul"


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["personal", "team", "legacy"])
async def test_api_source_matches_config_soul_and_origin(catalog, monkeypatch, source):
    from app.gateway.routers import agents

    monkeypatch.setattr(agents, "_require_agents_api_enabled", lambda: None)
    monkeypatch.setattr(agents, "get_effective_user_id", lambda: "viewer")
    monkeypatch.setattr(agents, "get_paths", lambda: catalog)
    monkeypatch.setattr(agents, "get_agent_store", lambda: FileAgentStore())
    result = await agents.get_agent("reports", source=source)
    assert result.source == source
    assert result.description == source
    assert result.soul == source + " soul"
