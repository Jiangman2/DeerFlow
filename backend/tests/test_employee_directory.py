"""Shared hot configuration, real creator names and server authorization."""

from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.gateway.employee_featured_store import FeaturedStore
from app.gateway.routers import employee_directory as directory
from deerflow.persistence.user.model import UserRow

OLD = directory.EmployeeIdentity("team", "research", "creator")
EXTERNAL = directory.EmployeeIdentity("external", "external", "")
NEW = directory.EmployeeIdentity("team", "new-employee", "creator", "new-publication")


def body(*, is_hot=False, version=1, identity=OLD):
    return {"is_hot": is_hot, "expected_version": version, "expected_identity": identity.token}


@pytest_asyncio.fixture
async def client(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'directory.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(UserRow.__table__.create)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        session.add(UserRow(id="creator", email="private@example.test", display_name="张研究员"))
        await session.commit()
    monkeypatch.setattr(directory, "get_session_factory", lambda: sessions)
    monkeypatch.setattr(directory, "_catalog", lambda _user: [OLD, EXTERNAL, NEW, directory.EmployeeIdentity("personal", "mine", "viewer")])
    monkeypatch.setattr(directory, "_require_agents_api_enabled", lambda: None)
    monkeypatch.setenv("DEERFLOW_EMPLOYEE_FEATURED_PATH", str(tmp_path / "hot.json"))
    FeaturedStore().initialize([OLD, EXTERNAL], actor="release")
    app = FastAPI()

    @app.middleware("http")
    async def auth(request, call_next):
        if request.headers.get("x-role"):
            from app.gateway.auth_disabled import AUTH_SOURCE_SESSION

            request.state.auth_source = AUTH_SOURCE_SESSION
            request.state.user = SimpleNamespace(id="viewer", system_role=request.headers["x-role"])
        return await call_next(request)

    app.include_router(directory.router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as api:
        yield api
    await engine.dispose()


@pytest.mark.asyncio
async def test_existing_and_newly_published_hot_private_not_hot_and_trusted_creator(client):
    response = await client.get("/api/employee-directory", headers={"x-role": "user"})
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    entries = response.json()["employees"]
    assert [entry["is_hot"] for entry in entries] == [True, True, True, False]
    assert [entry["version"] for entry in entries] == [1, 1, 0, 0]
    assert entries[0]["creator_name"] == "张研究员" and entries[1]["creator_name"] is None
    assert "private@example.test" not in response.text and "owner_id" not in response.text


@pytest.mark.asyncio
async def test_admin_remove_add_version_conflict_and_durable_audit(client):
    url = "/api/employee-directory/team/research/hot"
    assert (await client.put(url, json=body(), headers={"x-role": "user"})).status_code == 403
    first = await client.put(url, json=body(), headers={"x-role": "admin"})
    assert first.status_code == 200 and first.json()["version"] == 2 and not first.json()["is_hot"]
    assert (await client.put(url, json=body(), headers={"x-role": "admin"})).status_code == 409
    second = await client.put(url, json=body(is_hot=True, version=2), headers={"x-role": "admin"})
    assert second.status_code == 200 and second.json()["version"] == 3
    audits = FeaturedStore().snapshot().document["changes"]
    assert len(audits) == 2 and audits[0]["actor"] == "viewer" and audits[0]["is_hot"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("source,name,status", [("personal", "mine", 422), ("team", "unknown", 404), ("invalid", "research", 422)])
async def test_cannot_feature_private_or_unknown_employee(client, source, name, status):
    response = await client.put(f"/api/employee-directory/{source}/{name}/hot", json=body(), headers={"x-role": "admin"})
    assert response.status_code == status


@pytest.mark.asyncio
async def test_new_employee_can_be_removed_by_admin(client):
    response = await client.put("/api/employee-directory/team/new-employee/hot", json=body(is_hot=False, version=0, identity=NEW), headers={"x-role": "admin"})
    assert response.status_code == 200 and response.json()["is_hot"] is False
    entries = (await client.get("/api/employee-directory", headers={"x-role": "user"})).json()["employees"]
    assert entries[2]["is_hot"] is False and entries[2]["version"] == 1


@pytest.mark.asyncio
async def test_republished_name_rejects_stale_browser_identity(client, monkeypatch):
    assert (await client.put("/api/employee-directory/team/research/hot", json=body(), headers={"x-role": "admin"})).status_code == 200
    replacement = directory.EmployeeIdentity("team", "research", "creator", "republished")
    monkeypatch.setattr(directory, "_catalog", lambda _: [replacement])
    entries = (await client.get("/api/employee-directory", headers={"x-role": "user"})).json()["employees"]
    assert entries[0]["is_hot"] is False and entries[0]["version"] == 2
    assert (await client.put("/api/employee-directory/team/research/hot", json=body(version=2), headers={"x-role": "admin"})).status_code == 409
    response = await client.put("/api/employee-directory/team/research/hot", json=body(is_hot=True, version=2, identity=replacement), headers={"x-role": "admin"})
    assert response.status_code == 200 and response.json()["is_hot"] is True


@pytest.mark.asyncio
async def test_no_database_is_explicitly_unavailable(client, monkeypatch):
    monkeypatch.setattr(directory, "get_session_factory", lambda: None)
    assert (await client.get("/api/employee-directory", headers={"x-role": "user"})).status_code == 503


@pytest.mark.asyncio
async def test_missing_shared_file_not_reseeded_by_read_or_write(client, monkeypatch, tmp_path):
    path = tmp_path / "missing.json"
    monkeypatch.setenv("DEERFLOW_EMPLOYEE_FEATURED_PATH", str(path))
    assert (await client.get("/api/employee-directory", headers={"x-role": "user"})).status_code == 503
    assert (await client.put("/api/employee-directory/team/research/hot", json=body(), headers={"x-role": "admin"})).status_code == 503
    assert not path.exists()


@pytest.mark.asyncio
async def test_strict_body_and_anonymous_cannot_modify(client):
    url = "/api/employee-directory/team/research/hot"
    assert (await client.put(url, json=body())).status_code == 401
    for invalid in ({**body(), "is_hot": "true"}, {**body(), "creator_name": "forged"}, {"is_hot": True, "expected_version": 0}):
        assert (await client.put(url, json=invalid, headers={"x-role": "admin"})).status_code == 422


def test_external_catalog_identity_parity():
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    names = set()
    for filename in ("shared-agents.ts", "contest-agents.ts"):
        names.update(re.findall(r'name: "([^"]+)"', (root / "frontend/src/core/agents" / filename).read_text(encoding="utf-8")))
    assert names == set(directory.EXTERNAL_EMPLOYEE_NAMES)


def test_hot_feature_adds_no_sql_schema():
    from deerflow.persistence import models  # noqa: F401
    from deerflow.persistence.base import Base

    assert "employee_featured" not in Base.metadata.tables
