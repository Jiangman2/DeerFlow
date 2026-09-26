"""Regression anchors: the custom-agent router must not block the event loop.

``app.gateway.routers.agents.create_agent_endpoint`` and ``delete_agent`` are
async route handlers that resolve the configured agent store and then create or
remove records. Store selection may resolve ``config.yaml`` and the file backend
resolves/probes agent directories, so both store initialization and CRUD must run
inside ``asyncio.to_thread``. If either regresses back onto the event loop, the
strict Blockbuster gate raises ``BlockingError`` and these tests fail.

Imports live at module scope so the one-time FastAPI app construction (which
reads files while building OpenAPI schemas) happens at collection time, not on
the event loop under test. Test-side path resolution is itself offloaded with
``asyncio.to_thread`` (matching ``test_uploads_middleware``) so only the
handlers' own filesystem access is exercised on the loop.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.gateway.routers.agents import AgentCreateRequest, create_agent_endpoint, delete_agent
from deerflow.config.agents_api_config import load_agents_api_config_from_dict
from deerflow.config.paths import get_paths
from deerflow.runtime.user_context import get_effective_user_id

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _use_isolated_file_agent_store(tmp_path: Path, monkeypatch):
    """Keep the blocking gate independent of a developer's SQL config.

    The deliberate path probe in ``resolve_store`` also proves that callers do
    not eagerly evaluate ``get_agent_store()`` before entering ``to_thread``.
    """
    from deerflow.persistence.agents.file import FileAgentStore

    store = FileAgentStore()
    store_probe = tmp_path / "agent-store-probe"
    store_probe.mkdir()

    def resolve_store() -> FileAgentStore:
        store_probe.resolve().exists()
        return store

    monkeypatch.setattr("deerflow.persistence.agents.get_agent_store", resolve_store)
    monkeypatch.setattr("app.gateway.routers.agents.get_agent_store", resolve_store)


async def test_create_agent_does_not_block_event_loop(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DEER_FLOW_HOME", str(tmp_path))
    monkeypatch.setattr("deerflow.config.paths._paths", None)
    load_agents_api_config_from_dict({"enabled": True})
    try:
        response = await create_agent_endpoint(AgentCreateRequest(name="loop-make-agent", soul="You are a test agent."))
        assert response is not None

        user_id = get_effective_user_id()
        # test-side check (resolution offloaded; not exercised on the loop)
        agent_dir = await asyncio.to_thread(get_paths().user_agent_dir, user_id, "loop-make-agent")
        assert await asyncio.to_thread((agent_dir / "config.yaml").exists)
    finally:
        load_agents_api_config_from_dict({})


async def test_delete_agent_does_not_block_event_loop(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DEER_FLOW_HOME", str(tmp_path))
    monkeypatch.setattr("deerflow.config.paths._paths", None)
    load_agents_api_config_from_dict({"enabled": True})
    try:
        user_id = get_effective_user_id()
        # test-side seeding (resolution offloaded; not exercised on the loop)
        agent_dir = await asyncio.to_thread(get_paths().user_agent_dir, user_id, "loop-test-agent")
        await asyncio.to_thread(agent_dir.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread((agent_dir / "config.yaml").write_text, "name: loop-test-agent\n", encoding="utf-8")

        # The delete route requires the definition the caller reviewed. Resolve
        # the store and read it off-loop, just as an API detail request does.
        from deerflow.persistence.agents import get_agent_store

        definition = await asyncio.to_thread(lambda: get_agent_store().read_definition("loop-test-agent", user_id=user_id))
        await delete_agent("loop-test-agent", expected_draft_revision=definition.revision)

        assert not await asyncio.to_thread(agent_dir.exists)
    finally:
        load_agents_api_config_from_dict({})
