from __future__ import annotations

from uuid import UUID

import pytest
from _router_auth_helpers import make_authed_test_app
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.gateway.auth.models import User
from app.gateway.routers import site_navigation
from deerflow.persistence.base import Base

pytestmark = pytest.mark.asyncio


def _admin_user() -> User:
    return User(
        id=UUID("11111111-2222-3333-4444-555555555555"),
        email="admin@example.com",
        password_hash="x",
        system_role="admin",
    )


def _regular_user() -> User:
    return User(
        id=UUID("99999999-8888-7777-6666-555555555555"),
        email="user@example.com",
        password_hash="x",
        system_role="user",
    )


async def _session_factory():
    import deerflow.persistence.models  # noqa: F401

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sf = async_sessionmaker(engine, expire_on_commit=False)
    return sf, engine


def _make_client(sf, monkeypatch: pytest.MonkeyPatch, user_factory=_admin_user):
    app = make_authed_test_app(user_factory=user_factory)
    app.include_router(site_navigation.router)
    monkeypatch.setattr(site_navigation, "get_session_factory", lambda: sf)
    return TestClient(app)


async def test_admin_can_manage_site_navigation(monkeypatch: pytest.MonkeyPatch) -> None:
    sf, engine = await _session_factory()
    try:
        with _make_client(sf, monkeypatch) as client:
            created = client.post(
                "/api/admin/site-navigation",
                json={
                    "name": "勘探开发图文智能检索工具",
                    "url": "http://10.57.128.79:8100/",
                    "aliases": ["勘探开发检索", "图文检索", "图文检索"],
                    "description": "企业内网检索工具",
                    "category": "内网工具",
                    "sort_order": 10,
                    "enabled": True,
                },
            )
            assert created.status_code == 200
            item = created.json()["item"]
            assert item["created_by_email"] == "admin@example.com"
            assert item["aliases"] == ["勘探开发检索", "图文检索"]

            public_list = client.get("/api/site-navigation")
            assert public_list.status_code == 200
            assert public_list.json()["items"][0]["name"] == "勘探开发图文智能检索工具"

            updated = client.patch(
                f"/api/admin/site-navigation/{item['id']}",
                json={"enabled": False},
            )
            assert updated.status_code == 200
            assert updated.json()["item"]["enabled"] is False

            public_after_disable = client.get("/api/site-navigation")
            assert public_after_disable.status_code == 200
            assert public_after_disable.json()["items"] == []

            deleted = client.delete(f"/api/admin/site-navigation/{item['id']}")
            assert deleted.status_code == 200
            assert deleted.json()["success"] is True
    finally:
        await engine.dispose()


async def test_non_admin_cannot_manage_site_navigation(monkeypatch: pytest.MonkeyPatch) -> None:
    sf, engine = await _session_factory()
    try:
        with _make_client(sf, monkeypatch, _regular_user) as client:
            response = client.post(
                "/api/admin/site-navigation",
                json={
                    "name": "内部站点",
                    "url": "https://example.internal",
                    "aliases": [],
                    "description": "",
                    "category": "",
                    "sort_order": 0,
                    "enabled": True,
                },
            )
            assert response.status_code == 403
    finally:
        await engine.dispose()


async def test_rejects_non_http_site_url(monkeypatch: pytest.MonkeyPatch) -> None:
    sf, engine = await _session_factory()
    try:
        with _make_client(sf, monkeypatch) as client:
            response = client.post(
                "/api/admin/site-navigation",
                json={
                    "name": "Bad URL",
                    "url": "javascript:alert(1)",
                    "aliases": [],
                    "description": "",
                    "category": "",
                    "sort_order": 0,
                    "enabled": True,
                },
            )
            assert response.status_code == 422
    finally:
        await engine.dispose()
