"""Admin-only, durable public catalog overrides; private employees stay untouched."""

from uuid import UUID

import pytest
import pytest_asyncio
from _router_auth_helpers import make_authed_test_app
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.gateway.auth.models import User
from app.gateway.routers import agents
from deerflow.config.agents_api_config import load_agents_api_config_from_dict
from deerflow.persistence.base import Base


@pytest_asyncio.fixture
async def square_env(monkeypatch, tmp_path):
    monkeypatch.setenv("DEER_FLOW_HOME", str(tmp_path))
    monkeypatch.setattr("deerflow.config.paths._paths", None)
    load_agents_api_config_from_dict({"enabled": True})
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sf = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(agents, "get_session_factory", lambda: sf)
    monkeypatch.setattr(agents, "get_effective_user_id", lambda: str(UUID(int=1)))
    yield sf
    load_agents_api_config_from_dict({})
    await engine.dispose()


def client(role="admin", user_id=1):
    app = make_authed_test_app(user_factory=lambda: User(id=UUID(int=user_id), email="dev@example.com", password_hash="x", system_role=role))

    @app.middleware("http")
    async def session_source(request, call_next):
        request.state.auth_source = "session"
        return await call_next(request)

    app.include_router(agents.router)
    return TestClient(app)


@pytest.mark.asyncio
async def test_admin_changes_persist_and_stale_update_is_rejected(square_env):
    with client() as c:
        key = "external:专业文档解析专家"
        result = c.put("/api/digital-employee-square/settings", json={"key": key, "version": 0, "display_name": "测试展示名称", "sort_order": 1})
        assert result.status_code == 200, result.text
        assert result.json()["item"]["version"] == 1
        stale = c.put("/api/digital-employee-square/settings", json={"key": key, "version": 0, "hidden": True})
        assert stale.status_code == 409
        result = c.put("/api/digital-employee-square/settings", json={"key": key, "version": 1, "hidden": True})
        assert result.status_code == 200
    with client("user") as c:
        items = c.get("/api/digital-employee-square/settings").json()["items"]
        assert items[0]["hidden"] is True
        assert items[0]["metadata"]["display_name"] == "测试展示名称"
        assert items[0]["sort_order"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [{"hidden": True}, {"display_name": "伪造管理员"}, {"sort_order": 1}])
async def test_regular_user_cannot_manage_even_with_forged_role(square_env, changes):
    with client("user") as c:
        result = c.put("/api/digital-employee-square/settings", json={"key": "external:专业文档解析专家", "version": 0, **changes, "system_role": "admin"})
        assert result.status_code in {403, 422}
        assert c.put("/api/digital-employee-square/settings", json={"key": "external:专业文档解析专家", "version": 0, **changes}).status_code == 403
        assert c.delete("/api/team-agents/some-agent").status_code == 403
        assert c.get("/api/digital-employee-square/settings").json()["items"] == []


@pytest.mark.asyncio
async def test_anonymous_cannot_manage(square_env):
    app = FastAPI()
    app.include_router(agents.router)
    with TestClient(app) as c:
        assert c.put("/api/digital-employee-square/settings", json={"key": "external:工具", "version": 0, "hidden": True}).status_code == 401
        assert c.get("/api/digital-employee-square/settings").status_code == 401


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [{"external_url": "javascript:alert(1)"}, {"sort_order": -1}, {"tags": ["x" * 21]}, {"display_name": " "}])
async def test_invalid_public_metadata_is_rejected(square_env, changes):
    with client() as c:
        assert c.put("/api/digital-employee-square/settings", json={"key": "external:工具", "version": 0, **changes}).status_code == 422


@pytest.mark.asyncio
async def test_admin_can_withdraw_another_publishers_employee_without_deleting_private_data(square_env, tmp_path):
    import yaml

    team_dir = tmp_path / "team_agents" / "other-employee"
    team_dir.mkdir(parents=True)
    (team_dir / "config.yaml").write_text(yaml.safe_dump({"name": "other-employee", "description": "Original", "published_by_user_id": "another-user"}), encoding="utf-8")
    (team_dir / "SOUL.md").write_text("Keep this behavior", encoding="utf-8")
    with client() as c:
        assert c.delete("/api/team-agents/other-employee").status_code == 204
        assert c.get("/api/team-agents").json()["agents"] == []
        assert len(c.get("/api/team-agents?include_hidden=true").json()["agents"]) == 1
        setting = c.get("/api/digital-employee-square/settings").json()["items"][0]
        assert c.put("/api/digital-employee-square/settings", json={"key": setting["key"], "version": setting["version"], "hidden": False}).status_code == 200
        assert len(c.get("/api/team-agents").json()["agents"]) == 1
    with client("user") as c:
        assert c.get("/api/team-agents?include_hidden=true").status_code == 403
    assert (team_dir / "SOUL.md").read_text(encoding="utf-8") == "Keep this behavior"


@pytest.mark.asyncio
async def test_regular_users_keep_private_edit_and_new_publish_but_cannot_overwrite_square(square_env):
    from deerflow.config.app_config import AppConfig, reset_app_config, set_app_config
    from deerflow.config.sandbox_config import SandboxConfig

    set_app_config(AppConfig(sandbox=SandboxConfig(use="deerflow.sandbox.local:LocalSandboxProvider")))
    try:
        with client("user") as c:
            assert c.post("/api/agents", json={"name": "public-test", "description": "First"}).status_code == 201
            assert c.post("/api/agents/public-test/publish", json={"overwrite": True}).status_code == 200
            assert c.put("/api/agents/public-test", json={"description": "Private change"}).status_code == 200
            assert c.get("/api/team-agents/public-test").json()["description"] == "First"
            assert c.post("/api/agents/public-test/publish", json={"overwrite": True}).status_code == 403
            assert c.get("/api/team-agents/public-test").json()["description"] == "First"
            with client("user", user_id=2) as other:
                assert other.delete("/api/team-agents/public-test").status_code == 403
                assert other.put("/api/digital-employee-square/settings", json={"key": "team:public-test", "version": 0, "hidden": True}).status_code == 403
            assert c.put("/api/digital-employee-square/settings", json={"key": "team:public-test", "version": 0, "hidden": True, "sort_order": 0}).status_code == 403
            assert c.put("/api/digital-employee-square/settings", json={"key": "team:public-test", "version": 0, "hidden": True}).status_code == 200
            assert c.get("/api/team-agents").json()["agents"] == []
            assert c.get("/api/agents/public-test").json()["description"] == "Private change"
            assert c.put("/api/digital-employee-square/settings", json={"key": "team:public-test", "version": 1, "hidden": False}).status_code == 403
            assert c.delete("/api/team-agents/public-test").status_code == 204
    finally:
        reset_app_config()


@pytest.mark.asyncio
@pytest.mark.parametrize("publisher_role,withdrawer_role", [("user", "user"), ("user", "admin"), ("admin", "admin")])
async def test_withdrawn_employee_can_be_republished_by_original_publisher(square_env, monkeypatch, publisher_role, withdrawer_role):
    from deerflow.config.app_config import AppConfig, reset_app_config, set_app_config
    from deerflow.config.sandbox_config import SandboxConfig

    set_app_config(AppConfig(sandbox=SandboxConfig(use="deerflow.sandbox.local:LocalSandboxProvider")))
    try:
        with client(publisher_role) as owner:
            assert owner.post("/api/agents", json={"name": "republish-test", "description": "First"}).status_code == 201
            assert owner.post("/api/agents/republish-test/publish", json={"overwrite": True}).status_code == 200
            with client("admin", user_id=3) as admin:
                assert admin.put("/api/digital-employee-square/settings", json={"key": "team:republish-test", "version": 0, "sort_order": 5, "display_name": "Reviewed name"}).status_code == 200
            with client(withdrawer_role) as withdrawer:
                assert withdrawer.delete("/api/team-agents/republish-test").status_code == 204
            assert owner.get("/api/team-agents").json()["agents"] == []
            assert owner.put("/api/agents/republish-test", json={"description": "New private description"}).status_code == 200
            # Another account's same-name private employee must not replace the
            # withdrawn original, even though new publications remain open.
            monkeypatch.setattr(agents, "get_effective_user_id", lambda: str(UUID(int=2)))
            with client("user", user_id=2) as other:
                assert other.post("/api/agents", json={"name": "republish-test"}).status_code == 201
                assert other.post("/api/agents/republish-test/publish", json={"overwrite": True}).status_code == 403
            monkeypatch.setattr(agents, "get_effective_user_id", lambda: str(UUID(int=1)))
            result = owner.post("/api/agents/republish-test/publish", json={"overwrite": True})
            assert result.status_code == 200, result.text
            visible = owner.get("/api/team-agents").json()["agents"]
            assert len(visible) == 1
            assert visible[0]["description"] == "New private description"
            assert visible[0]["display_name"] == "Reviewed name"
            setting = next(item for item in owner.get("/api/digital-employee-square/settings").json()["items"] if item["key"] == "team:republish-test")
            assert setting["hidden"] is False
            assert setting["sort_order"] == 5
    finally:
        reset_app_config()


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["auth_disabled", "internal"])
async def test_synthetic_admin_is_not_a_signed_in_administrator(square_env, source):
    app = make_authed_test_app(user_factory=lambda: User(id=UUID(int=1), email="synthetic@example.com", password_hash="x", system_role="admin"))

    @app.middleware("http")
    async def fake_source(request, call_next):
        request.state.auth_source = source
        return await call_next(request)

    app.include_router(agents.router)
    with TestClient(app) as c:
        assert c.put("/api/digital-employee-square/settings", json={"key": "external:工具", "version": 0, "hidden": True}).status_code == 403
        assert c.delete("/api/team-agents/tool").status_code == 403


@pytest.mark.asyncio
async def test_republish_does_not_undo_concurrent_admin_withdrawal(square_env, monkeypatch):
    from contextlib import asynccontextmanager
    from deerflow.config.app_config import AppConfig, reset_app_config, set_app_config
    from deerflow.config.sandbox_config import SandboxConfig
    from deerflow.persistence.digital_employee_square import DigitalEmployeeSquareRepository

    set_app_config(AppConfig(sandbox=SandboxConfig(use="deerflow.sandbox.local:LocalSandboxProvider")))
    original_edit = DigitalEmployeeSquareRepository.edit
    withdraw_after_publication = False

    @asynccontextmanager
    async def competing_withdrawal(repo, key):
        nonlocal withdraw_after_publication
        async with original_edit(repo, key) as edit:
            yield edit
        # A competing writer now waits for publication's entire transaction,
        # then reads a fresh version. It must not be silently undone afterwards.
        if withdraw_after_publication:
            withdraw_after_publication = False
            current = await repo.get(key)
            await repo.patch(key, version=current["version"], metadata={}, updated_by="another-admin", hidden=True)

    try:
        with client("user") as c:
            assert c.post("/api/agents", json={"name": "concurrent-republish"}).status_code == 201
            assert c.post("/api/agents/concurrent-republish/publish", json={"overwrite": True}).status_code == 200
            assert c.delete("/api/team-agents/concurrent-republish").status_code == 204
            withdraw_after_publication = True
            monkeypatch.setattr(DigitalEmployeeSquareRepository, "edit", competing_withdrawal)
            assert c.post("/api/agents/concurrent-republish/publish", json={"overwrite": True}).status_code == 200
            assert c.get("/api/team-agents").json()["agents"] == []
    finally:
        reset_app_config()
